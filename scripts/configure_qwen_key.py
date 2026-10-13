"""Save a Beijing Model Studio API key without echoing it or putting it in shell history."""
from getpass import getpass
from pathlib import Path
import re

path = Path(__file__).resolve().parents[1] / '.env'
key = getpass('百炼北京按量付费 API Key（输入不回显）：').strip()
if not key or not re.fullmatch(r'[A-Za-z0-9_-]+', key):
    raise SystemExit('Key 为空或格式不合法，未写入。')
if key.startswith('sk-sp-'):
    raise SystemExit('此入口使用按量付费 Key，不适用 Coding Plan 套餐 Key。')
content = path.read_text() if path.exists() else ''
line = 'DASHSCOPE_API_KEY=' + key
if re.search(r'^DASHSCOPE_API_KEY=.*$', content, re.M):
    content = re.sub(r'^DASHSCOPE_API_KEY=.*$', lambda _: line, content, flags=re.M)
else:
    content += '\n' + line + '\n'
path.write_text(content)
path.chmod(0o600)
print('已保存到项目 .env。重启 Python API 和 Worker 后生效。')
