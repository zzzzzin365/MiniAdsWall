import tempfile,unittest
from pathlib import Path
from evaluation.evidence_pipeline import assemble,hand_fact,select_fields,supplemental_observation,repair,sha

class EvidencePipelineTests(unittest.TestCase):
 def test_missing_hand_is_unknown(self):
  obs=[{'key':'product','value':'一条手链','state':'observed','source_id':'im','observation_id':'o'}]
  self.assertIsNone(hand_fact(obs))
 def test_handmade_is_not_human_hand(self):
  self.assertIsNone(hand_fact([{'key':'material','value':'handmade bracelet','state':'observed','source_id':'im','observation_id':'o'}]))
 def test_false_requires_explicit_absence(self):
  o={'key':'hand','value':'no human hands visible','state':'unclear','source_id':'im','observation_id':'o'}
  self.assertIsNone(hand_fact([o]));o['state']='explicitly_absent';self.assertIs(hand_fact([o])['value'],False)
 def test_missing_context_not_replaced_with_distractor(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_bytes(b'pixels');ctx={'id':'real','body':'actual text','publisher_record':'X'};asset={'path':str(p),'sha256':sha(p)};registry={sha(p):{'context':ctx}}
   self.assertEqual(assemble({'context_pool':[{'id':'wrong'}]},asset,registry),[])
   self.assertEqual(assemble({'context_pool':[ctx],'links':[{'context_id':'wrong'}]},asset,registry),[ctx])
   p.write_bytes(b'changed');self.assertEqual(assemble({'context_pool':[ctx]},asset,registry),[])
 def test_supplement_shape_and_scope(self):
  with self.assertRaises(ValueError):supplemental_observation({'hand_present':False,'state':'present','region':'all'},'im','r')
  o=supplemental_observation({'hand_present':False,'state':'absent','region':'all'},'im','r');self.assertIs(hand_fact([o])['value'],False)
 def test_repair_does_not_retain_fabricated_quote_or_unasked_fields(self):
  ctx={'id':'text','body':'A real source sentence long enough for citation.','publisher_record':'Recorded Name'}
  result,_=repair('quote',{'publisher':'invented','quote':'invented'},'im',[],[ctx])
  self.assertIsNone(result['publisher']);self.assertIn(result['quote'],ctx['body']);self.assertEqual(result['text_citations'],['text'])
 def test_repair_never_defaults_unknown_to_false(self):
  result,_=repair('cross',{},'im',[],[{'id':'text','publisher_record':'X'}])
  self.assertIsNone(result['hand_present']);self.assertEqual(result['status'],'partial')
 def test_selection_excludes_unrequested_body(self):
  obs,ctx=select_fields('publisher',[{'key':'hand','value':'human hand','state':'observed'}],[{'id':'t','body':'secret extra data','publisher_record':'X'}]);self.assertEqual(obs,[]);self.assertNotIn('body',ctx[0])
if __name__=='__main__':unittest.main()
