"""Move the isolated workspace to a recoverable backup, only while preview is stopped."""
from datetime import datetime
from pathlib import Path
import socket
import sys

root = Path(__file__).resolve().parents[1]
for port in (8000, 5173):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            sys.exit('请先停止本地预览，再恢复开场资料；当前资料未修改。')
workspace = root / 'backend' / 'presentation-data'
if workspace.is_symlink():
    sys.exit('资料目录为链接，未执行重置。')
if workspace.exists():
    if not (workspace / '.presentation-workspace').is_file():
        sys.exit('资料目录缺少归属标识，未执行重置。')
    backup = root / 'backups' / ('presentation-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.parent.mkdir(parents=True, exist_ok=True)
    workspace.rename(backup)
    print(f'原工作空间已保留到 {backup}；重新启动时将准备新的开场资料。')
