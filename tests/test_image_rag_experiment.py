"""No paid/network calls: guard uncertain submissions, replay, budget and hashes."""
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.image_rag_provider import Provider
from evaluation.run_image_rag_effect import data_url, reader_content, grade, check_answer


class FakeHTTP:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise TimeoutError('uncertain submission')
        class Response:
            status_code = 200
            is_success = True
            def json(self):
                return {'id':'fake-receipt', 'usage':{'prompt_tokens':2, 'completion_tokens':1},
                        'choices':[{'finish_reason':'stop','message':{'content':'{"value":true}'}}]}
        return Response()


class TestImageRagExperiment(unittest.TestCase):
    def provider(self, root, fail=False, budget=160):
        p = Provider.__new__(Provider)
        p.out = root
        p.receipts = root / 'receipts'
        p.receipts.mkdir()
        p.key = 'fixture-credential-never-written'
        p.url = 'https://provider.invalid/chat'
        p.http = FakeHTTP(fail)
        p.max_calls = budget
        return p

    def test_timeout_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.provider(Path(directory), fail=True)
            with self.assertRaises(RuntimeError):
                p.call('uncertain', 'fake-model', [{'role':'user','content':'test'}])
            with self.assertRaises(RuntimeError):
                p.call('uncertain', 'fake-model', [{'role':'user','content':'test'}])
            self.assertEqual(p.http.calls, 1)
            files = list(p.receipts.glob('*.json'))
            self.assertEqual(json.loads(files[0].read_text())['status'], 'failed_or_submission_unknown')
            self.assertNotIn(p.key, files[0].read_text())

    def test_success_replay_and_call_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            p = self.provider(Path(directory), budget=1)
            _, _, first_replay = p.call('one', 'fake-model', [{'role':'user','content':'test'}])
            response, _, second_replay = p.call('one', 'fake-model', [{'role':'user','content':'test'}])
            self.assertFalse(first_replay)
            self.assertTrue(second_replay)
            self.assertEqual(response, {'value':True})
            with self.assertRaises(RuntimeError):
                p.call('two', 'fake-model', [{'role':'user','content':'test'}])
            self.assertEqual(p.http.calls, 1)

    def test_changed_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.jpg'
            path.write_bytes(b'changed bytes')
            with self.assertRaises(RuntimeError):
                data_url({'path':str(path), 'sha256':'0'*64, 'mime':'image/jpeg'})

    def test_unknown_reference_and_boolean_guess_not_accepted(self):
        with self.assertRaises(RuntimeError):
            check_answer({'status':'answered','answer':'yes','facts':{'visible':True},'source_ids':['other']}, ['allowed'])
        case = {'answerable':True,'expected':{'visible':True},'gold_source_ids':['allowed']}
        response = {'status':'answered','facts':{'visible':'true'},'source_ids':['allowed']}
        self.assertFalse(grade(case,response)['grounded_correct'])

    def test_reader_receives_source_name_binding(self):
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.jpg'
            path.write_bytes(b'fixture-image')
            asset={'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest(),
                   'mime':'image/jpeg','name':'明确的素材标题'}
            content=reader_content({'question':'标题对应哪张图？','required_fields':['surface']},['source1'],{'source1':asset})
            self.assertEqual(json.loads(content[0]['text'])['source_name_map'],{'source1':'明确的素材标题'})
            self.assertIn('material_name=明确的素材标题',content[1]['text'])


if __name__ == '__main__':
    unittest.main()
