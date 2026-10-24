"""Per-request model receipts without storing prompts or private source text."""
import contextvars
import time

receipts = contextvars.ContextVar('rag_model_receipts', default=None)


async def observed_create(client, *, stage, **kwargs):
    started = time.monotonic()
    row = {'stage':stage, 'model':kwargs.get('model'), 'status':'failed',
           'input_tokens':None, 'output_tokens':None, 'provider_cost':None}
    try:
        response = await client.messages.create(**kwargs)
        usage = getattr(response, 'usage', None)
        if isinstance(usage, dict):
            row.update(input_tokens=usage.get('input_tokens',usage.get('prompt_tokens')),
                output_tokens=usage.get('output_tokens',usage.get('completion_tokens')),provider_cost=usage.get('cost'))
        elif usage is not None:
            row.update(input_tokens=getattr(usage,'input_tokens',None),output_tokens=getattr(usage,'output_tokens',None))
        row['status']='succeeded'
        return response
    finally:
        row['duration_ms']=(time.monotonic()-started)*1000
        target=receipts.get()
        if target is not None:
            target.append(row)
