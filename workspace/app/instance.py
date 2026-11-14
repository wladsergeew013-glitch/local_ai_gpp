"""Single owner per Workspace data directory on Windows and Linux."""
import os


class Instance:
    def __init__(self,path):self.path=path;self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b')
        self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None;raise RuntimeError('WORKSPACE_ALREADY_RUNNING: this data directory already has an owner.') from None
        return self
    def __exit__(self,*_):
        if self.file:self.file.close();self.file=None
