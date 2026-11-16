import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const spotchecks={
 list:()=>api('/spotchecks'),
 get:id=>api('/spotchecks/'+id),
 claim:data=>api('/spotchecks',{method:'POST',body:JSON.stringify(data)}),
 report:(id,data)=>api('/spotchecks/'+id+'/reports',{method:'POST',body:JSON.stringify(data)}),
 close:(id,data)=>api('/spotchecks/'+id+'/close',{method:'POST',body:JSON.stringify(data||{})}),
 cancel:id=>api('/spotchecks/'+id+'/cancel',{method:'POST'})
};
