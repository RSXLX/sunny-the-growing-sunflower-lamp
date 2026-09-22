"""Advisory exclusive ownership of a local data directory (macOS/Linux)."""
import fcntl
import os
import stat
from pathlib import Path


class DataBusy(RuntimeError):pass


class DataLock:
    def __init__(self,directory):
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.fd=None
        fd=os.open(self.directory/'.bloom.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise DataBusy('数据锁不是普通文件')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise DataBusy('数据目录正在使用；先停止 BLOOM 服务或其他维护命令') from exc
            self.fd=fd
        except BaseException:os.close(fd);raise
    def close(self):
        if self.fd is not None:
            os.close(self.fd);self.fd=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
