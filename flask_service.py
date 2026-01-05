import os
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil
from werkzeug.serving import make_server

from app import app as flask_app


class FlaskService(win32serviceutil.ServiceFramework):
    _svc_name_ = "FlaskService"
    _svc_display_name_ = "Flask Service"
    _svc_description_ = "Flask API service"

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.server = None
        self.thread = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.server:
            self.server.shutdown()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "9443"))
        self.server = make_server(host, port, flask_app)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        servicemanager.LogInfoMsg(f"Flask service started on {host}:{port}")
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)
        servicemanager.LogInfoMsg("Flask service stopped")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(FlaskService)
