"""Read only reviewed, scoped generic procedures. No simulated outcome evidence."""
from .context_service import enabled
class ExperienceProvider:
    def __init__(self, repo=None): self.repo=repo
    def search(self, scope, query):
        if not enabled('AGENT_EXPERIENCE_READ_ENABLED') or self.repo is None:
            return {'status':'disabled','reason':'experience_read_disabled','items':[]}
        from marketing.feedback.procedures import ProcedureRepository
        return {'status':'complete','reason':None,'items':ProcedureRepository(self.repo).search(scope,query)}
