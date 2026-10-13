"""Service-authenticated asynchronous Seedance video generation."""
import os
import re
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from core.model_gate import get_model_gate, ModelBusy

router = APIRouter(prefix='/generate/video', tags=['video'])
DEFAULT_MODEL = 'doubao-seedance-1-0-pro-fast-251015'

class VideoInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=3000)
    image_url: str | None = Field(default=None, max_length=8192)
    resolution: Literal['480p', '720p', '1080p'] = '480p'
    ratio: Literal['16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'] = '9:16'
    duration: int = Field(default=5, ge=2, le=12)
    seed: int = Field(default=-1, ge=-1, le=4294967295)
    camera_fixed: bool = False
    watermark: bool = True

    @field_validator('prompt')
    @classmethod
    def clean_prompt(cls, value):
        if not value.strip(): raise ValueError('prompt 不能为空')
        # Parameters belong in the body, not weakly validated prompt suffixes.
        if re.search(r'--(?:rs|rt|dur|seed|cf|wm)\b', value):
            raise ValueError('请通过请求字段设置参数，不使用提示词参数后缀')
        return value.strip()

    @field_validator('image_url')
    @classmethod
    def validate_image(cls, value):
        if value is not None and not value.startswith('https://'):
            raise ValueError('首帧图片需要 HTTPS URL')
        return value

async def provider_request(method, path, payload=None, request_id=''):
    key = os.getenv('ARK_API_KEY', '').strip()
    if not key: raise HTTPException(503, 'ARK_API_KEY 未配置')
    base = os.getenv('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')
    try:
        async with get_model_gate().slot(), httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, base + path, headers={
                'Authorization': f'Bearer {key}', 'X-Request-ID': request_id}, json=payload)
            if response.status_code >= 400:
                code = ''
                try: code = str(response.json().get('error', {}).get('code', ''))
                except (ValueError, AttributeError): pass
                raise HTTPException(502, {'message':'Seedance 请求失败', 'provider_status':response.status_code, 'code':code})
            data = response.json()
            if not isinstance(data, dict) or (data.get('error') and not data.get('status')):
                raise ValueError('invalid provider response')
            return data
    except ModelBusy as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, '请求超时，任务可能已创建；请在方舟任务列表核查，勿直接重复提交') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Seedance 连接或响应异常') from exc

async def create_task(body: VideoInput, request_id=''):
    content = [{'type':'text', 'text':body.prompt}]
    if body.image_url:
        content.append({'type':'image_url', 'image_url':{'url':body.image_url}, 'role':'first_frame'})
    payload = body.model_dump(exclude={'prompt','image_url'})
    payload.update(model=os.getenv('SEEDANCE_MODEL', DEFAULT_MODEL), content=content)
    data = await provider_request('POST', '/contents/generations/tasks', payload, request_id)
    if not isinstance(data.get('id'), str) or not data['id']:
        raise HTTPException(502, 'Seedance 未返回任务 ID；请先核查方舟任务列表')
    return {'id':data['id'], 'model':payload['model']}

async def get_task(task_id: str, request_id=''):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        raise HTTPException(422, '任务 ID 不合法')
    data = await provider_request('GET', '/contents/generations/tasks/' + task_id, request_id=request_id)
    if data.get('status') not in {'queued','running','succeeded','failed','cancelled','expired'}:
        raise HTTPException(502, 'Seedance 返回未知任务状态')
    if data['status'] == 'succeeded' and not (data.get('content') or {}).get('video_url'):
        raise HTTPException(502, '任务成功但没有视频地址')
    return data

@router.post('')
async def create(body: VideoInput, request: Request):
    return await create_task(body, request.state.request_id)

@router.get('/{task_id}')
async def query(task_id: str, request: Request):
    return await get_task(task_id, request.state.request_id)
