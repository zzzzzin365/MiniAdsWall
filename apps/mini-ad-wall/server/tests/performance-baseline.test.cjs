const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const ts=require('typescript');
const source=fs.readFileSync(path.join(__dirname,'../../client/src/utils/performance.ts'),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const mod={exports:{}};new Function('module','exports',compiled)(mod,mod.exports);
const {performanceForAd,materialPerformance}=mod.exports;
const baseline=require('./helpers/performance-baseline.json');
test('performance preserves original panel values for both date ranges',()=>{
 for(const range of ['7','3']) for(const [index,ad] of baseline.ads.entries()){
  const {id,...expected}=baseline.periods[range][index];
  assert.deepEqual(performanceForAd(ad,range),expected);
  assert.deepEqual(performanceForAd({...ad,clicks:999999,price:50,version:99},range),expected,'configuration and raw click counter do not change the panel baseline');
 }
});
test('shared material totals add each referenced ad exactly once',()=>{
 for(const range of ['7','3']){
  const result=materialPerformance(baseline.ads.map(ad=>({id:ad.id,video_count:ad.videos.length})),range);
  for(const key of ['impressions','clicks','orders','spend','revenue'])assert.equal(result[key],baseline.periods[range].reduce((n,item)=>n+item[key],0));
 }
});
