from pydantic import BaseModel, Field
class SpotClaimInput(BaseModel):
    dataset_id: int
    size: int = Field(ge=1, le=1000)
    model: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=160)
    request_no: str | None = Field(default=None, min_length=1, max_length=64)
class SpotReportInput(BaseModel):
    asset: str = Field(min_length=1, max_length=80)
    km: float
    failed: int = Field(ge=0, le=1)
    report_no: str = Field(min_length=1, max_length=64)
class SpotCloseInput(BaseModel):
    close_no: str | None = Field(default=None, min_length=1, max_length=64)
