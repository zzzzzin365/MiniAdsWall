import fs from 'fs';
import os from 'os';
import { performance } from 'perf_hooks';
import { Engine } from '../recall/engine';
import { Document } from '../recall/contracts';
async function main(){
 const rows=Number(process.argv[2]||100000),queries=Number(process.argv[3]||200),output=process.argv[4];
 if(!Number.isInteger(rows)||rows<1||rows>10000000||!Number.isInteger(queries)||queries<1||queries>10000)throw Error('usage: benchmark-recall ROWS QUERIES [OUTPUT]');
 const engine=new Engine(),began=performance.now();let accepted=0;const latency:number[]=[],errors:Record<string,number>={};
 try{
  for(let start=1;start<=rows;start+=500){const docs:Document[]=[];for(let id=start;id<=Math.min(rows,start+499);id++)docs.push({doc_id:id,index_revision:'1',attributes:{'1':[String(1+id%10)],'2':Array.from({length:9},(_,i)=>String(100+i*1000+(id*31+i*17)%1000))},enabled:id%20!==0,deleted:false,starts_at:null,ends_at:null,score:`${1+id%100}.00000000`});await engine.load(docs);if(process.memoryUsage().rss>8*1024**3)throw Error('benchmark_memory_budget_exceeded');}
  const buildMs=performance.now()-began;
  for(let i=0;i<queries;i++){const start=performance.now();let token:string|undefined;try{const result=await engine.open([{field:'1',op:'in',values:[String(1+i%10)]},{field:'2',op:'not_in',values:[String(100+i%1000)],missing:'exclude'}],50,Date.now());token=result.token;accepted++;latency.push(performance.now()-start);}catch(e:any){errors[e.message]=(errors[e.message]||0)+1;}finally{if(token)await engine.release(token);}}
  latency.sort((a,b)=>a-b);const percentile=(p:number)=>latency[Math.max(0,Math.ceil(latency.length*p)-1)]??null;
  const result={scope:'in_memory_bitmap_and_rank_workers_only',database_tested:false,koa_tested:false,rows,average_attribute_values:10,term_count_bound:9010,queries,accepted,errors,build_ms:buildMs,p50_ms:percentile(.5),p95_ms:percentile(.95),p99_ms:percentile(.99),rss_bytes:process.memoryUsage().rss,hardware:{platform:process.platform,arch:process.arch,node:process.version,cpus:os.cpus().length,total_memory:os.totalmem()},measured_at:new Date().toISOString()};
  if(output)fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
 }finally{await engine.close();}
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
