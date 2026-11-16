"""Maintenance spot checks over in-service assets.

Business rules enforced here:

* a roster is drawn only from assets that are still in service (``failed == 0``)
  in the requested dataset, and both the roster and the mileage at draw time are
  frozen persistently;
* one asset may belong to at most one *unfinished* spot check across all batches
  (the reservation table is the cross-batch guard);
* an order that cannot be filled completely is rejected as a whole;
* every item has to be reported with mileage and failure status, mileage may
  never move backwards, and a missing report blocks closing and sample creation;
* closing creates a *new* dataset from the reported observations and leaves the
  source dataset untouched;
* closing, reporting and cancelling are serialized per check, so only one final
  decision can win;
* request numbers make claim/report/close retries idempotent, and a reused
  report number with different content is rejected;
* all roster, reservation and progress state lives in the database, so an
  interrupted process recovers by reading it back (plus a startup repair).
"""
import hashlib
import hmac
import json
import math
import random
import threading
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from src.models import Dataset, SpotCheck, SpotCheckItem, SpotReservation, SpotReport
from src.services.audit import record
from src.utils.serialization import checksum, dataset_dict

_LOCK_REGISTRY = threading.Lock()
_CHECK_LOCKS: dict[int, threading.Lock] = {}
_CLAIM_LOCK = threading.Lock()

#: Analysis contract: mileage outside this range is rejected.
MIN_KM = 1.0
MAX_KM = 10_000_000.0


def _check_lock(check_id: int) -> threading.Lock:
    with _LOCK_REGISTRY:
        return _CHECK_LOCKS.setdefault(check_id, threading.Lock())


def _normalise(value) -> str:
    return str(value if value is not None else '').strip()


def _hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _public_item(item: SpotCheckItem) -> dict:
    return {
        'position': item.position,
        'asset': item.asset,
        'model': item.model,
        'original_km': item.original_km,
        'reported_km': item.reported_km,
        'reported_failed': item.reported_failed,
        'report_no': item.report_no,
        'reported_at': item.reported_at,
        'reported': item.report_no is not None,
    }


def present(db, check: SpotCheck, replayed: bool | None = None) -> dict:
    """Build the API view of a check from persisted state (recovery friendly)."""
    items = list(db.scalars(select(SpotCheckItem).where(SpotCheckItem.check_id == check.id).order_by(SpotCheckItem.position)))
    reported = [item for item in items if item.report_no is not None]
    missing = [item for item in items if item.report_no is None]
    source = db.get(Dataset, check.dataset_id)
    generated = db.get(Dataset, check.result_dataset_id) if check.result_dataset_id else None
    data = {
        'id': check.id,
        'name': check.name,
        'dataset_id': check.dataset_id,
        'dataset_name': source.name if source else None,
        'model': check.model,
        'size': check.size,
        'status': check.status,
        'request_no': check.request_no,
        'created_by': check.created_by,
        'created_at': check.created_at,
        'closed_at': check.closed_at,
        'cancelled_at': check.cancelled_at,
        'close_no': check.close_no,
        'result_dataset_id': check.result_dataset_id,
        'roster_checksum': check.roster_checksum,
        'reported_count': len(reported),
        'missing_count': len(missing),
        'complete': check.size == len(reported),
        'items': [_public_item(item) for item in items],
        'missing': [_public_item(item) for item in missing],
        'generated_dataset': dataset_dict(generated) if generated else None,
    }
    if replayed is not None:
        data['replayed'] = replayed
    return data


def listing(db) -> list[dict]:
    return [present(db, check) for check in db.scalars(select(SpotCheck).order_by(SpotCheck.id.desc()).limit(100))]


def detail(db, check_id: int) -> dict:
    check = db.get(SpotCheck, check_id)
    if not check:
        raise HTTPException(404, '抽检单不存在')
    return present(db, check)


def _in_service_candidates(dataset: Dataset, model: str | None) -> list[tuple[str, str, float]]:
    """Merge dataset rows by asset and keep assets that are still in service.

    An asset with any failed observation is treated as failed (not drawable). An
    asset appearing more than once is kept only when every row says it is still
    in service; the largest mileage is frozen as its original mileage.
    """
    rows_by_asset: dict[str, list[tuple[str, float, int]]] = {}
    for row in dataset.records or []:
        if not isinstance(row, dict):
            continue
        asset = _normalise(row.get('asset'))
        if not asset:
            continue
        try:
            km = float(row.get('km'))
            failed = int(float(row.get('failed')))
            model_value = _normalise(row.get('model'))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(km) or failed not in (0, 1):
            continue
        rows_by_asset.setdefault(asset, []).append((model_value, km, failed))

    candidates: list[tuple[str, str, float]] = []
    for asset in sorted(rows_by_asset):
        rows = rows_by_asset[asset]
        if any(failed != 0 for _, _, failed in rows):
            continue
        if model is not None and any(value != model for value, _, _ in rows):
            continue
        model_value = rows[0][0]
        original_km = max(km for _, km, _ in rows)
        if not MIN_KM <= original_km <= MAX_KM:
            continue
        candidates.append((asset, model_value, original_km))
    return candidates


def _locked_check(db, check_id: int) -> SpotCheck | None:
    """Load a check under a row lock so decisions serialize across processes."""
    return db.scalar(select(SpotCheck).where(SpotCheck.id == check_id).with_for_update())


def _replay_claim(db, user, request_no: str, request_hash: str) -> dict | None:
    existing = db.scalar(select(SpotCheck).where(
        SpotCheck.created_by == user.id,
        SpotCheck.request_no == request_no,
    ))
    if not existing:
        return None
    if not hmac.compare_digest(existing.request_hash, request_hash):
        raise HTTPException(409, '领取请求号已用于不同内容，拒绝重复抽取')
    return present(db, existing, replayed=True)


def _commit(db):
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _serialize_claims(db):
    """Take a transaction-scoped database lock when the backend supports it.

    The in-process lock covers a single API process; the advisory lock keeps two
    API processes from drawing overlapping rosters at the same instant. A retry
    that loses the race still replays through ``request_no``.
    """
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(841017)'))


def claim(db, data, user, request_no: str | None = None) -> dict:
    """Draw a frozen roster; idempotent by ``request_no`` per user."""
    request_no = _normalise(request_no) or _normalise(getattr(data, 'request_no', None)) or uuid.uuid4().hex
    if len(request_no) > 64:
        raise HTTPException(422, '领取请求号最多 64 个字符')
    model = _normalise(getattr(data, 'model', None)) or None
    request_hash = _hash({'dataset_id': data.dataset_id, 'size': data.size, 'model': model})

    with _CLAIM_LOCK:
        try:
            _serialize_claims(db)
            replayed = _replay_claim(db, user, request_no, request_hash)
            if replayed is not None:
                _commit(db)
                return replayed

            dataset = db.get(Dataset, data.dataset_id)
            if not dataset:
                raise HTTPException(404, '数据批次不存在')

            free_assets = set(db.scalars(select(SpotReservation.asset)))
            candidates = [row for row in _in_service_candidates(dataset, model) if row[0] not in free_assets]
            if len(candidates) < data.size:
                scope = f'型号 {model} ' if model else ''
                raise HTTPException(409, f'{scope}在役件仅 {len(candidates)} 件可用于本次抽检，少于申请的 {data.size} 件，整单未领取')

            drawn = random.SystemRandom().sample(candidates, data.size)
            drawn.sort(key=lambda row: row[0])
            now = time.time()
            name = _normalise(getattr(data, 'name', None)) or f'机务抽检 · {dataset.name} · {data.size}件'
            check = SpotCheck(
                name=name[:160],
                dataset_id=dataset.id,
                model=model,
                size=data.size,
                status='open',
                request_no=request_no,
                request_hash=request_hash,
                created_by=user.id,
                created_at=now,
                roster_checksum=_hash([{'asset': asset, 'model': value, 'original_km': km} for asset, value, km in drawn]),
            )
            db.add(check)
            db.flush()
            for position, (asset, model_value, original_km) in enumerate(drawn):
                db.add(SpotCheckItem(check_id=check.id, asset=asset, model=model_value, original_km=original_km, position=position))
                db.add(SpotReservation(asset=asset, check_id=check.id, created_at=now))
            db.flush()
            record(db, user.email, 'spotcheck.claim', {'id': check.id, 'dataset': dataset.id, 'size': data.size, 'request_no': request_no})
            data_out = present(db, check, replayed=False)
            _commit(db)
            return data_out
        except IntegrityError:
            db.rollback()
            # Another process may have completed the very same retry first.
            try:
                replayed = _replay_claim(db, user, request_no, request_hash)
            except HTTPException:
                raise
            if replayed is not None:
                _commit(db)
                return replayed
            raise HTTPException(409, '在役件已被其他未结抽检占用，整单未领取，请重试') from None
        except Exception:
            db.rollback()
            raise


def report(db, check_id: int, data, user) -> dict:
    """Record one item report; idempotent by report number, rejects rollback."""
    report_no = _normalise(data.report_no)
    if not report_no or len(report_no) > 64:
        raise HTTPException(422, '回报号须为 1 至 64 个字符')
    asset = _normalise(data.asset)
    km = float(data.km)
    failed = int(data.failed)
    payload_hash = _hash({'asset': asset, 'km': km, 'failed': failed})

    lock = _check_lock(check_id)
    with lock:
        try:
            check = _locked_check(db, check_id)
            if not check:
                raise HTTPException(404, '抽检单不存在')

            previous = db.scalar(select(SpotReport).where(
                SpotReport.check_id == check_id,
                SpotReport.report_no == report_no,
            ))
            if previous:
                if not hmac.compare_digest(previous.payload_hash, payload_hash):
                    raise HTTPException(409, '回报号已用于不同内容，拒绝覆盖')
                data_out = present(db, check, replayed=True)
                _commit(db)
                return data_out

            if check.status == 'cancelled':
                raise HTTPException(409, '抽检单已撤销，拒收迟到回报')
            if check.status == 'closed':
                raise HTTPException(409, '抽检单已结案，拒收迟到回报')

            item = db.scalar(select(SpotCheckItem).where(
                SpotCheckItem.check_id == check_id,
                SpotCheckItem.asset == asset,
            ))
            if not item:
                raise HTTPException(422, '该资产不在本次冻结名单内')
            if item.report_no is not None:
                raise HTTPException(409, '该资产已回报，不能使用新回报号重复提交')

            if not math.isfinite(km) or not MIN_KM <= km <= MAX_KM:
                raise HTTPException(422, '回报里程须为1至1000万公里的有限数值')
            if km < item.original_km:
                raise HTTPException(422, f'回报里程 {km:.1f} 小于领取时冻结的原里程 {item.original_km:.1f}，拒绝倒退')

            now = time.time()
            db.add(SpotReport(
                check_id=check_id,
                report_no=report_no,
                asset=item.asset,
                km=km,
                failed=failed,
                payload_hash=payload_hash,
                received_at=now,
            ))
            item.reported_km = km
            item.reported_failed = failed
            item.report_no = report_no
            item.reported_at = now
            db.flush()
            record(db, user.email, 'spotcheck.report', {'id': check_id, 'asset': item.asset, 'report_no': report_no, 'km': km, 'failed': failed})
            data_out = present(db, check, replayed=False)
            _commit(db)
            return data_out
        except IntegrityError:
            db.rollback()
            # A concurrent identical retry may already have stored this report.
            try:
                check = _locked_check(db, check_id)
                previous = db.scalar(select(SpotReport).where(
                    SpotReport.check_id == check_id,
                    SpotReport.report_no == report_no,
                ))
            except Exception:
                raise HTTPException(409, '该资产或回报号已被并发提交，未生成重复样本') from None
            if check and previous and hmac.compare_digest(previous.payload_hash, payload_hash):
                data_out = present(db, check, replayed=True)
                _commit(db)
                return data_out
            raise HTTPException(409, '该资产或回报号已被并发提交，未生成重复样本') from None
        except Exception:
            db.rollback()
            raise


def _unique_dataset_name(db, base: str) -> str:
    name = base[:160]
    if not db.scalar(select(Dataset.id).where(Dataset.name == name)):
        return name
    for index in range(2, 1000):
        suffix = f'（{index}）'
        candidate = base[:160 - len(suffix)] + suffix
        if not db.scalar(select(Dataset.id).where(Dataset.name == candidate)):
            return candidate
    raise HTTPException(409, '无法生成唯一的批次名称，请先整理同名批次')


def close(db, check_id: int, close_no: str | None, user) -> dict:
    """Close a fully reported check and create the new sample dataset once."""
    close_no = _normalise(close_no) or f'close-{check_id}'
    lock = _check_lock(check_id)
    with lock:
        try:
            check = _locked_check(db, check_id)
            if not check:
                raise HTTPException(404, '抽检单不存在')

            if check.status == 'closed':
                data_out = present(db, check, replayed=True)
                _commit(db)
                return data_out
            if check.status == 'cancelled':
                raise HTTPException(409, '抽检单已撤销，不能结案')

            items = list(db.scalars(select(SpotCheckItem).where(SpotCheckItem.check_id == check_id).order_by(SpotCheckItem.position)))
            missing = [item for item in items if item.report_no is None]
            if missing:
                names = '、'.join(item.asset for item in missing[:10])
                more = f' 等 {len(missing)} 件' if len(missing) > 10 else ''
                raise HTTPException(409, f'尚有 {len(missing)} 件未回报（{names}{more}），整单不结案、不生成样本')

            records = [
                {
                    'asset': item.asset,
                    'model': item.model,
                    'km': item.reported_km,
                    'failed': item.reported_failed,
                }
                for item in items
            ]
            source = db.get(Dataset, check.dataset_id)
            base_name = f'{source.name} · 抽检 S{check.id} 回报' if source else f'抽检 S{check.id} 回报'
            dataset = Dataset(
                name=_unique_dataset_name(db, base_name),
                source=f'spot-check-{check.id}.json',
                records=records,
                checksum=checksum(records),
                created_at=time.time(),
            )
            db.add(dataset)
            db.flush()
            check.status = 'closed'
            check.closed_at = time.time()
            check.result_dataset_id = dataset.id
            check.close_no = close_no
            db.execute(delete(SpotReservation).where(SpotReservation.check_id == check.id))
            db.flush()
            record(db, user.email, 'spotcheck.close', {'id': check.id, 'dataset': dataset.id, 'close_no': close_no, 'samples': len(records)})
            data_out = present(db, check, replayed=False)
            _commit(db)
            return data_out
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, '结案已被并发处理，未重复建批') from None
        except Exception:
            db.rollback()
            raise


def cancel(db, check_id: int, user) -> dict:
    """Cancel an unfinished check, release its assets and reject late reports."""
    lock = _check_lock(check_id)
    with lock:
        try:
            check = _locked_check(db, check_id)
            if not check:
                raise HTTPException(404, '抽检单不存在')
            if check.status == 'closed':
                raise HTTPException(409, '抽检单已结案，不能撤销')
            if check.status == 'cancelled':
                data_out = present(db, check, replayed=True)
                _commit(db)
                return data_out

            check.status = 'cancelled'
            check.cancelled_at = time.time()
            db.execute(delete(SpotReservation).where(SpotReservation.check_id == check.id))
            db.flush()
            record(db, user.email, 'spotcheck.cancel', {'id': check.id, 'reported': sum(1 for i in db.scalars(select(SpotCheckItem).where(SpotCheckItem.check_id == check.id)) if i.report_no)})
            data_out = present(db, check, replayed=False)
            _commit(db)
            return data_out
        except Exception:
            db.rollback()
            raise


def restore_reservations(db) -> int:
    """Repair persisted occupancy after an interrupted process.

    Removes reservations of checks that are no longer open and re-creates any
    missing reservation rows for open checks. Roster and received progress are
    already durable on their own; this only realigns the cross-batch guard.
    """
    open_checks = list(db.scalars(select(SpotCheck).where(SpotCheck.status == 'open')))
    open_ids = [check.id for check in open_checks]
    if open_ids:
        db.execute(delete(SpotReservation).where(SpotReservation.check_id.notin_(open_ids)))
    else:
        db.execute(delete(SpotReservation))
    reserved = set(db.scalars(select(SpotReservation.asset)))
    restored = 0
    for check in open_checks:
        for item in db.scalars(select(SpotCheckItem).where(SpotCheckItem.check_id == check.id)):
            if item.asset in reserved:
                continue
            db.add(SpotReservation(asset=item.asset, check_id=check.id, created_at=time.time()))
            reserved.add(item.asset)
            restored += 1
    db.commit()
    return restored
