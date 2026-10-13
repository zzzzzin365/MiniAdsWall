"""Securely save a local Ark key; optionally verify it with one small API call."""
import argparse
from getpass import getpass
from pathlib import Path
import re
import sys

from dotenv import dotenv_values
import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true', help='Verify saved key with a small paid API request')
args = parser.parse_args()
path = Path(__file__).resolve().parents[1] / '.env'
if not args.check:
    key = getpass('火山方舟北京 API Key（不回显）：').strip()
    if not key or not re.fullmatch(r'[A-Za-z0-9_-]+', key):
        raise SystemExit('Key 为空或格式不合法，未写入。')
    content = path.read_text() if path.exists() else ''
    for name, value in {'ARK_API_KEY':key, 'LLM_PROVIDER':'seed'}.items():
        line = name + '=' + value
        pattern = '^' + name + '=.*$'
        if re.search(pattern, content, re.M):
            content = re.sub(pattern, lambda _: line, content, flags=re.M)
        else:
            content += '\n' + line + '\n'
    path.write_text(content)
    path.chmod(0o600)
    print('已保存 Seed Key 并选择 Seed。重启 API 和 Worker 后生效。')
else:
    config = dotenv_values(path)
    key = config.get('ARK_API_KEY')
    if not key:
        raise SystemExit('ARK_API_KEY 未配置。')
    try:
        response = httpx.post(config.get('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/') + '/chat/completions',
            headers={'Authorization':'Bearer ' + key},
            json={'model':config.get('ARK_MODEL','doubao-seed-2-1-lite-260915'),
                  'messages':[{'role':'user','content':'只回复 OK'}], 'max_tokens':16,
                  'thinking':{'type':'disabled'}}, timeout=30)
        if response.status_code != 200:
            raise SystemExit('连接失败，HTTP ' + str(response.status_code) + '；请检查 Key、模型开通和余额。')
        data = response.json()
        if data.get('error') or not data['choices'][0]['message'].get('content'):
            raise SystemExit('模型未返回有效文本。')
        print('Seed 真实调用成功；用量：', data.get('usage', {}))
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        raise SystemExit('连接或响应异常；未输出密钥。')
