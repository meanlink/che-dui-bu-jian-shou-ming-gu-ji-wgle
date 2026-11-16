/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
export {};
/** @typedef {{id:number,name:string,dataset_id:number,model:string|null,size:number,status:string,reported_count:number,missing_count:number,items:Array<Object>,missing:Array<Object>,generated_dataset:Object|null}} SpotCheck */
