const {test}=require('node:test');const assert=require('node:assert/strict');
const {Partition,compare}=require('../dist/recall/partition');
const {search,attributes,matches}=require('../dist/recall/contracts');
const doc=(id,attrs={},score='1.00000000')=>({doc_id:id,index_revision:'1',attributes:attrs,enabled:true,deleted:false,starts_at:null,ends_at:null,score});
test('recall contract rejects invalid filters and distinguishes missing attributes',()=>{
 for(const value of [{conditions:[{field:'tags',op:'in',values:[]}]},{conditions:[{field:'tags',op:'in',values:['x'],missing:'include'}]},{limit:101},{conditions:[{field:'bad-key',op:'exists'}]}])assert.throws(()=>search(value));
 const fields=[{field_key:'tags',field_id:'1',cardinality:'multi',max_values:50},{field_key:'region',field_id:'2',cardinality:'scalar',max_values:1}];
 assert.deepEqual({...attributes({tags:['e\u0301','é']},fields)},{tags:['é']});assert.throws(()=>attributes({region:['a','b']},fields));
 assert.equal(matches(doc(1),[{field:'1',op:'not_in',values:['1'],missing:'exclude'}],Date.now()),false);
 assert.equal(matches(doc(1),[{field:'1',op:'not_in',values:['1'],missing:'include'}],Date.now()),true);
});
test('bitmap IN/NOT IN, immutable query roots, tombstones and exact decimal ranking',()=>{
 const p=new Partition(1);let seq=0;
 for(const d of [doc(1,{'1':['1','4']},'100.00000001'),doc(33,{'1':['4']},'100.00000000'),doc(65,{'1':['5']},'100.00000000'),doc(97)])p.apply(String(++seq),d);
 const conditions=[{field:'1',op:'in',values:['4','5']},{field:'1',op:'not_in',values:['1','2','3'],missing:'exclude'}];
 assert.deepEqual([...p.cursor(conditions,Date.now(),50)].map(d=>d.doc_id),[33,65]);
 const pinned=p.cursor([],Date.now(),50);const first=pinned.next().value;assert.equal(first.doc_id,1);
 p.apply(String(++seq),{...doc(33,{'1':['4']}),index_revision:'2',enabled:false});
 assert.ok([...pinned].some(d=>d.doc_id===33));assert.ok(![...p.cursor([],Date.now(),50)].some(d=>d.doc_id===33));
 p.apply(String(++seq),{...doc(65),index_revision:'2',deleted:true});
 p.apply(String(++seq),{...doc(65),index_revision:'1'});assert.ok(![...p.cursor([],Date.now(),50)].some(d=>d.doc_id===65));
 assert.throws(()=>p.apply('999',doc(129)),/sequence_gap/);
});
test('deterministic randomized bitmap results equal a row-by-row reference',()=>{
 for(let seed=1;seed<=20;seed++){
  let state=seed;const random=()=>((state=(Math.imul(state,1664525)+1013904223)>>>0)/4294967296);
  const parts=Array.from({length:32},(_,i)=>new Partition(i)),docs=[];
  for(let id=1;id<=1000;id++){const d=doc(id,{},`${Math.floor(random()*20)}.00000000`);d.enabled=random()>.1;for(let field=1;field<=3;field++){const values=[];for(let term=1;term<=5;term++)if(random()<.2)values.push(String(field*10+term));if(values.length)d.attributes[String(field)]=values;}docs.push(d);parts[id%32].load(d);}
  for(let q=0;q<100;q++){const conditions=[];for(let i=0;i<1+Math.floor(random()*3);i++){const field=1+Math.floor(random()*3),op=['in','not_in','exists','missing'][Math.floor(random()*4)];conditions.push({field:String(field),op,...(['in','not_in'].includes(op)?{values:[String(field*10+1),String(field*10+2)],...(op==='not_in'?{missing:random()<.5?'include':'exclude'}:{})}:{})});}const expected=docs.filter(d=>matches(d,conditions,Date.now())).sort(compare).map(d=>d.doc_id);const actual=parts.flatMap(p=>[...p.cursor(conditions,Date.now(),100,1000)]).sort(compare).map(d=>d.doc_id);assert.deepEqual(actual,expected,`seed=${seed},query=${q}`);}
 }
});
