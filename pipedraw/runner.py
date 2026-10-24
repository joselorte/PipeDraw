"""Running NMRPipe csh scripts in the background."""
import os
import subprocess

from PySide6 import QtCore


class CshRunner(QtCore.QObject):
    """Runs ``csh <script>`` in the script's folder. csh reads ~/.cshrc,
    which is where NMRPipe's installer sets up nmrPipe."""
    output = QtCore.Signal(str)
    started = QtCore.Signal()
    finished = QtCore.Signal(int, float)   # exit code, seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self.clock = QtCore.QElapsedTimer()

    @property
    def running(self) -> bool:
        return self.proc is not None

    def elapsed(self) -> float:
        return self.clock.elapsed() / 1000.0 if self.clock.isValid() else 0.0

    def run(self, script_path):
        if self.running:
            return
        self.proc = QtCore.QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(script_path))
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(lambda code, _status: self._done(code))
        self.proc.errorOccurred.connect(self._error)
        self.output.emit(f"$ csh {os.path.basename(script_path)}\n")
        self.clock.start()
        self.started.emit()
        self.proc.start("/bin/csh", [os.path.basename(script_path)])

    def stop(self):
        if not self.proc:
            return
        pid = self.proc.processId()
        if pid:
            # The pipeline's processes are children of csh.
            subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
        self.proc.kill()
        self.output.emit("[stopped]\n")

    def _read(self):
        if self.proc:
            data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
            # NMRPipe's -verb progress redraws with carriage returns.
            self.output.emit(data.replace("\r\n", "\n").replace("\r", "\n"))

    def _error(self, err):
        if err == QtCore.QProcess.FailedToStart:
            self.output.emit("[could not start /bin/csh]\n")
            self._done(-1)

    def _done(self, code):
        if self.proc is None:
            return
        self._read()
        self.proc = None
        self.finished.emit(code, self.elapsed())
