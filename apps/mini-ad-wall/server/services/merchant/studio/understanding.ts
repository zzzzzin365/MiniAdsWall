import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { withAdsConnection } from '../../ads.database';
import { assetPath } from './assets';
import { authorize } from './store';
const exec=promisify(execFile);
export async function analysisInput(job:any) {
 const asset=await withAdsConnection(async c=>{const a=await assetPath(c,job.input.asset_id,job.environment);await authorize(c,job.created_by,a.row.brand_id,job.environment,true);if(a.row.brand_id!==job.brand_id)throw Error('asset_scope_mismatch');return a;});
 if(asset.row.data.mime.startsWith('image/')) {const buffer=await fs.readFile(asset.path);return {asset_id:asset.row.id,model:job.input.model,kind:'image',frames:[{data_url:`data:${asset.row.data.mime};base64,${buffer.toString('base64')}`,time_ms:null}]};}
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'studio-frames-'));
 try {const frames=[];const count=Math.min(8,Math.max(1,Math.ceil(Number(asset.row.data.duration)/3)));let total=0;
 for(let i=0;i<count;i++){const t=Number(asset.row.data.duration)*i/count,file=path.join(dir,`${i}.jpg`);await exec(process.env.STUDIO_FFMPEG||'ffmpeg',['-v','error','-ss',String(t),'-i',asset.path,'-frames:v','1','-vf','scale=min(1280\\,iw):-2','-q:v','4',file],{timeout:10000,maxBuffer:65536});const buffer=await fs.readFile(file);total+=buffer.length;if(total>16*1024*1024)throw Error('frame_limit');frames.push({data_url:'data:image/jpeg;base64,'+buffer.toString('base64'),time_ms:Math.round(t*1000)});}
 return {asset_id:asset.row.id,model:job.input.model,kind:'video',frames};
 }finally{await fs.rm(dir,{recursive:true,force:true});}
}
