import unittest
from evaluation.evidence_pipeline import repair
from evaluation.run_evidence_pipeline import validate,grade

class ContractTests(unittest.TestCase):
 def setUp(self):
  self.ctx={'id':'ctx','publisher_record':'Record','body':'This is a continuous genuine source sentence for a literal quote.'}
  self.asset={'sha256':'hash'};self.registry={'hash':{'context':self.ctx}};self.labels={'im':{'hand_present':False}}
  self.case={'case_id':'q','image_id':'im','task':'cross','cohort':'primary','context_pool':[self.ctx]}
  self.obs=[{'observation_id':'obs','source_id':'im','canonical_hand':False,'state':'explicitly_absent','value':'检查全图未发现真人手或手腕','key':'hand_presence','region':'all'}]
 def test_false_is_a_complete_answer_and_not_missing(self):
  answer,_=repair('cross',{},'im',self.obs,[self.ctx]);self.assertIs(answer['hand_present'],False);self.assertEqual(answer['status'],'answered')
  self.assertEqual(validate(answer,self.case,self.registry,self.asset),[])
  row={'image_id':'im','task':'cross','cohort':'primary','answer':answer}
  self.assertTrue(grade(row,self.case,self.labels,self.registry,self.asset)['joint_pass'])
 def test_extra_field_is_not_strictly_compliant(self):
  answer,_=repair('cross',{},'im',self.obs,[self.ctx]);answer['quote']='An extra quotation'
  self.assertIn('unrequested_quote',validate(answer,self.case,self.registry,self.asset))
 def test_wrong_source_citation_is_rejected(self):
  answer,_=repair('cross',{},'im',self.obs,[self.ctx]);answer['text_citations']=['other'];self.case['context_pool'].append({'id':'other'})
  self.assertIn('wrong_source_citation',validate(answer,self.case,self.registry,self.asset))
 def test_missing_source_refusal_is_not_visual_failure(self):
  self.case.update(task='publisher',cohort='stress_missing',context_pool=[])
  answer,_=repair('publisher',{},'im',[],[])
  row={'image_id':'im','task':'publisher','cohort':'stress_missing','answer':answer}
  self.assertTrue(grade(row,self.case,self.labels,self.registry,self.asset)['joint_pass'])
 def test_quote_repair_obeys_predeclared_length_and_span(self):
  self.case['task']='quote';answer,_=repair('quote',{'quote':'invented'},'im',[],[self.ctx])
  self.assertTrue(12<=len(answer['quote'])<=60);self.assertIn(answer['quote'],self.ctx['body']);self.assertEqual(validate(answer,self.case,self.registry,self.asset),[])
if __name__=='__main__':unittest.main()
