"""Worker client. Only the trusted executor has Docker access; never the model/tool container."""
import asyncio
import os
import httpx

class SandboxClient:
    def __init__(self,url=None,token=None):
        self.url=(url or os.environ['HOSTING_EXECUTOR_URL']).rstrip('/')
        self.token=token or os.environ['HOSTING_EXECUTOR_TOKEN']
    async def cleanup(self,rid):
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response=await client.post(f'{self.url}/runs/{rid}/cleanup',headers={'Authorization':f'Bearer {self.token}'})
            response.raise_for_status()
    async def execute(self,run,argv,timeout):
        try:
            async with httpx.AsyncClient(timeout=timeout+15, trust_env=False) as client:
                response=await client.post(f'{self.url}/execute',headers={'Authorization':f'Bearer {self.token}'},
                    json={'run_id':str(run['id']),'fence_token':run['fence_token'],'argv':argv,'timeout':timeout})
                response.raise_for_status()
                return response.json()
        except BaseException:
            # Cancellation must not abandon a live shell; errors propagate if cleanup cannot be confirmed.
            task=asyncio.create_task(self.cleanup(run['id']))
            await asyncio.shield(task)
            raise
