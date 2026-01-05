import os
import subprocess
import time

import servicemanager
import win32event
import win32service
import win32serviceutil


class NginxService(win32serviceutil.ServiceFramework):
    _svc_name_ = "nginx"
    _svc_display_name_ = "nginx"
    _svc_description_ = "Nginx reverse proxy"

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None

    def _nginx_root(self):
        return os.getenv("NGINX_ROOT", "C:\\nginx-1.28.0")

    def _nginx_exe(self):
        return os.path.join(self._nginx_root(), "nginx.exe")

    def _nginx_conf(self):
        return os.path.join(self._nginx_root(), "conf", "nginx.conf")

    def _start_nginx(self):
        nginx_exe = self._nginx_exe()
        nginx_root = self._nginx_root()
        nginx_conf = self._nginx_conf()
        args = [nginx_exe, "-p", nginx_root, "-c", nginx_conf]
        self.process = subprocess.Popen(args, cwd=nginx_root)
        time.sleep(1)

    def _stop_nginx(self):
        nginx_exe = self._nginx_exe()
        nginx_root = self._nginx_root()
        nginx_conf = self._nginx_conf()
        try:
            subprocess.run(
                [nginx_exe, "-s", "quit", "-p", nginx_root, "-c", nginx_conf],
                cwd=nginx_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            pass

        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                pass

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop_nginx()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("Nginx service starting")
        self._start_nginx()
        servicemanager.LogInfoMsg("Nginx service started")
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        servicemanager.LogInfoMsg("Nginx service stopped")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(NginxService)
