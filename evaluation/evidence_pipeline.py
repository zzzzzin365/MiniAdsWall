"""Deterministic evidence assembly/selection/repair. Never reads evaluation labels."""
import copy,hashlib,json,re
HAND=re.compile(r'人手|人的.{0,8}手|一只手|双手|手腕|手掌|手部|手指|指甲|小臂|前臂|肤色|\b(?:hands?|wrists?|palms?|fingers?|forearms?)\b',re.I)
UNCERTAIN=re.compile(r'无|未|没有|不确定|看不清|\b(?:no|not|unclear|unknown|uncertain|absent)\b',re.I)
REQUIRED={'visual':['hand_present'],'publisher':['publisher'],'cross':['hand_present','publisher'],'quote':['quote']}
ALL_FIELDS={'hand_present','publisher','quote'}

def sha(path):
 return hashlib.sha256(path.read_bytes()).hexdigest()

def assemble(case,asset,registry):
 """Only registered pixel hash and exact archived context can establish a join."""
 if sha(__import__('pathlib').Path(asset['path']))!=asset['sha256']:return []
 entry=registry.get(asset['sha256'])
 if not entry:return []
 return [copy.deepcopy(c) for c in case['context_pool'] if c==entry['context']]

def hand_observations(observations):
 return [copy.deepcopy(o) for o in observations if HAND.search(str(o.get('key',''))+' '+str(o.get('value','')))]

def hand_fact(observations):
 for o in observations:
  if type(o.get('canonical_hand')) is bool and o.get('state')==('observed' if o['canonical_hand'] else 'explicitly_absent'):
   return {'value':o['canonical_hand'],'observation_id':o['observation_id'],'source_id':o['source_id'],'region':o.get('region','')}
 for o in hand_observations(observations):
  text=o.get('value','')
  if o.get('state')=='explicitly_absent' and re.search(r'no (?:human )?(?:hands?|wrists?)|没有.{0,5}(?:真人)?手|未(?:出现|发现|显示).{0,5}(?:人手|真人|手腕|手部)|无(?:人手|真人手|手腕)',text,re.I):
   return {'value':False,'observation_id':o['observation_id'],'source_id':o['source_id'],'region':o.get('region','')}
  if o.get('state')=='observed' and HAND.search(text) and not UNCERTAIN.search(text):
   return {'value':True,'observation_id':o['observation_id'],'source_id':o['source_id'],'region':o.get('region','')}
 return None

def select_fields(task,observations,contexts):
 required=REQUIRED[task];obs=hand_observations(observations) if 'hand_present' in required else []
 ctx=[]
 if 'publisher' in required or 'quote' in required:
  for c in contexts:
   selected={'id':c['id']}
   if 'publisher' in required:selected['publisher_record']=c['publisher_record']
   if 'quote' in required:selected['body']=c['body']
   ctx.append(selected)
 return obs,ctx

def supplemental_observation(result,image_id,request_id):
 v=result.get('hand_present');state=result.get('state');region=result.get('region')
 if v is None and state=='unclear':return None
 if type(v) is not bool or state!=('present' if v else 'absent') or not isinstance(region,str) or not region.strip():
  raise ValueError('invalid_supplemental_hand_evidence')
 return {'observation_id':'supp-'+request_id,'source_id':image_id,'key':'hand_presence','value':'画面出现真人手或手腕' if v else '检查全图后未发现真人手或手腕','state':'observed' if v else 'explicitly_absent','region':region,'canonical_hand':v}

def quote_span(body):
 for line in body.splitlines():
  line=line.strip()
  if len(line)>=12 and 'http' not in line and not line.startswith(('#','*','[')):
   return line[:60]
 return body[:60] if len(body)>=12 else None

def repair(task,raw,image_id,observations,contexts):
 """Fill only requested structured fields from scoped evidence, not model guesses."""
 required=REQUIRED[task];result={'status':'insufficient','hand_present':None,'publisher':None,'quote':None,'answer':'当前证据不足','image_citations':[],'text_citations':[]};fact=hand_fact(observations)
 if 'hand_present' in required and fact is not None:
  result['hand_present']=fact['value'];result['image_citations']=[image_id]
 if contexts:
  c=contexts[0]
  if 'publisher' in required and c.get('publisher_record'):
   result['publisher']=c['publisher_record'];result['text_citations']=[c['id']]
  if 'quote' in required:
   q=quote_span(c.get('body',''))
   if q is not None:result['quote']=q;result['text_citations']=[c['id']]
 filled=sum(result[k] is not None for k in required)
 result['status']='answered' if filled==len(required) else 'partial' if filled else 'insufficient'
 result['answer']='已按同源证据填写所需字段' if filled else '当前证据不足，无法确定'
 return result,sorted(k for k in result if result[k]!=raw.get(k))
