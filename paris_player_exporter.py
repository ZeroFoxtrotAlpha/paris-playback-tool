#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PARIS Player/Exporter GUI (minimal-deps)
- Load a .paris timing file (CSV with header comments)
- Set tone frequency, sample rate, volume, ramp (fade) time
- Play audio through system player (platform-dependent, no extra libs)
- Export to WAV (always)
- Export to MP3 (if pydub+ffmpeg available)

Author: ChatGPT
License: MIT
"""

import os
import sys
import math
import struct
import tempfile
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Optional MP3 support
try:
    from pydub import AudioSegment  # requires pydub + ffmpeg in PATH
    HAVE_PYDUB = True
except Exception:
    HAVE_PYDUB = False

def parse_paris(path):
    """Parse a .paris (CSV) file into list of (duration_ms:int, value:int)."""
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            ln = line.strip()
            if not ln or ln.startswith('#'):
                continue
            if ln.lower().startswith('duration_ms'):
                continue
            parts = [p.strip() for p in ln.split(',')]
            if len(parts) != 2:
                continue
            try:
                d = int(parts[0])
                v = int(parts[1])
                v = 1 if v else 0
                if d >= 0:
                    rows.append((d, v))
            except Exception:
                continue
    if not rows:
        raise ValueError("No timing rows found in file.")
    return rows

def synthesize_pcm16(rows, freq_hz=700.0, samplerate=44100, volume=0.5, ramp_ms=5.0):
    """Synthesize PCM16 bytes from timing rows. Returns (bytes, num_samples)."""
    # Clamp/validate
    samplerate = int(max(8000, min(192000, samplerate)))
    volume = float(max(0.0, min(1.0, volume)))
    freq_hz = float(max(50.0, min(6000.0, freq_hz)))
    ramp_ms = float(max(0.0, min(50.0, ramp_ms)))
    ramp_samples = int(samplerate * (ramp_ms / 1000.0))

    frames = bytearray()
    two_pi = 2.0 * math.pi
    t = 0  # running sample index
    # We'll build each segment as needed
    for (dur_ms, val) in rows:
        seg_len = int(round(samplerate * (dur_ms / 1000.0)))
        if seg_len <= 0:
            continue
        if val == 1:
            # tone
            for n in range(seg_len):
                # Apply simple cosine fade-in/out to reduce clicks
                # Compute envelope multiplier env in [0,1]
                if ramp_samples > 0:
                    if n < ramp_samples:
                        env = 0.5 * (1 - math.cos(math.pi * n / ramp_samples))
                    elif seg_len - n <= ramp_samples:
                        k = seg_len - n
                        env = 0.5 * (1 - math.cos(math.pi * k / ramp_samples))
                    else:
                        env = 1.0
                else:
                    env = 1.0
                sample = volume * env * math.sin(two_pi * freq_hz * (t / samplerate))
                s = int(max(-1.0, min(1.0, sample)) * 32767.0)
                frames += struct.pack('<h', s)
                t += 1
        else:
            # silence
            frames += b'\x00\x00' * seg_len
            t += seg_len

    return bytes(frames), t

def write_wav(path, pcm_bytes, samplerate=44100, nchannels=1, sampwidth=2):
    import wave
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)  # 16-bit
        wf.setframerate(samplerate)
        wf.writeframes(pcm_bytes)

def which(cmd):
    return shutil.which(cmd) is not None

class PlayerThread(threading.Thread):
    def __init__(self, wav_path):
        super().__init__(daemon=True)
        self.wav_path = wav_path
        self.proc = None
        self.stop_requested = False

    def run(self):
        try:
            if sys.platform.startswith('win'):
                # winsound blocks, but we can stop via PlaySound(None, ...)
                import winsound
                winsound.PlaySound(self.wav_path, winsound.SND_FILENAME)
            elif sys.platform == 'darwin':
                if which('afplay'):
                    self.proc = subprocess.Popen(['afplay', self.wav_path])
                    self.proc.wait()
            else:
                # Linux/others: try aplay or paplay
                if which('aplay'):
                    self.proc = subprocess.Popen(['aplay', self.wav_path])
                    self.proc.wait()
                elif which('paplay'):
                    self.proc = subprocess.Popen(['paplay', self.wav_path])
                    self.proc.wait()
                else:
                    # No player found
                    pass
        except Exception:
            pass

    def stop(self):
        try:
            if sys.platform.startswith('win'):
                import winsound
                winsound.PlaySound(None, 0)
            else:
                if self.proc and self.proc.poll() is None:
                    self.proc.terminate()
        except Exception:
            pass

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PARIS Player / Exporter")
        self.geometry("720x560")
        self.minsize(700, 520)

        self.paris_path = None
        self.player_thread = None
        self.tmp_wav = None

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # File chooser
        ttk.Label(frm, text="Loaded .paris file:").grid(row=0, column=0, sticky='w')
        self.var_file = tk.StringVar(value="(none)")
        self.ent_file = ttk.Entry(frm, textvariable=self.var_file, state='readonly')
        self.ent_file.grid(row=0, column=1, columnspan=3, sticky='we', padx=(6,6))
        ttk.Button(frm, text="Open .paris...", command=self.on_open).grid(row=0, column=4, sticky='w')

        # Params
        ttk.Separator(frm).grid(row=1, column=0, columnspan=5, sticky='we', pady=(8,8))

        ttk.Label(frm, text="Tone frequency (Hz):").grid(row=2, column=0, sticky='w')
        self.var_freq = tk.StringVar(value="700")
        ttk.Entry(frm, textvariable=self.var_freq, width=10).grid(row=2, column=1, sticky='w', padx=(6,24))

        ttk.Label(frm, text="Sample rate (Hz):").grid(row=2, column=2, sticky='w')
        self.var_sr = tk.StringVar(value="44100")
        ttk.Entry(frm, textvariable=self.var_sr, width=10).grid(row=2, column=3, sticky='w', padx=(6,24))

        ttk.Label(frm, text="Volume (0.0–1.0):").grid(row=3, column=0, sticky='w')
        self.var_vol = tk.StringVar(value="0.6")
        ttk.Entry(frm, textvariable=self.var_vol, width=10).grid(row=3, column=1, sticky='w', padx=(6,24))

        ttk.Label(frm, text="Ramp (ms):").grid(row=3, column=2, sticky='w')
        self.var_ramp = tk.StringVar(value="5")
        ttk.Entry(frm, textvariable=self.var_ramp, width=10).grid(row=3, column=3, sticky='w', padx=(6,24))

        # Buttons
        ttk.Separator(frm).grid(row=4, column=0, columnspan=5, sticky='we', pady=(8,8))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=5, sticky='w')
        self.btn_play = ttk.Button(btns, text="Play", command=self.on_play, state='disabled')
        self.btn_play.pack(side='left', padx=(0,8))
        self.btn_stop = ttk.Button(btns, text="Stop", command=self.on_stop, state='disabled')
        self.btn_stop.pack(side='left', padx=(0,24))
        ttk.Button(btns, text="Export WAV", command=self.on_export_wav).pack(side='left', padx=(0,8))
        ttk.Button(btns, text="Export MP3", command=self.on_export_mp3).pack(side='left')

        # Log box
        ttk.Label(frm, text="Log:").grid(row=6, column=0, columnspan=5, sticky='w')
        self.txt_log = tk.Text(frm, height=12, wrap='word')
        self.txt_log.grid(row=7, column=0, columnspan=5, sticky='nsew')

        # Layout weights
        for c in range(5):
            frm.columnconfigure(c, weight=1 if c in (1,2,3) else 0)
        frm.rowconfigure(7, weight=1)

        self.log("Load a .paris file to begin.")

    def log(self, msg):
        self.txt_log.insert('end', msg + '\n')
        self.txt_log.see('end')

    def read_params(self):
        try:
            freq = float(self.var_freq.get())
            sr = int(float(self.var_sr.get()))
            vol = float(self.var_vol.get())
            ramp = float(self.var_ramp.get())
            if not (0.0 <= vol <= 1.0):
                raise ValueError("Volume out of range")
            return freq, sr, vol, ramp
        except Exception as e:
            messagebox.showerror("Invalid Parameters", f"Please enter valid numeric parameters.\n\n{e}")
            return None

    def on_open(self):
        path = filedialog.askopenfilename(
            title="Open .paris file",
            filetypes=[("PARIS files", "*.paris"), ("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            rows = parse_paris(path)
            self.paris_path = path
            self.var_file.set(path)
            self.btn_play['state'] = 'normal'
            self.log(f"Loaded {len(rows)} rows from: {path}")
        except Exception as e:
            messagebox.showerror("Open failed", f"Could not parse file:\n{e}")
            self.log(f"Error: {e}")

    def synthesize_to_wav(self, out_path):
        if not self.paris_path:
            messagebox.showwarning("No file", "Open a .paris file first.")
            return False
        params = self.read_params()
        if not params:
            return False
        freq, sr, vol, ramp = params
        try:
            rows = parse_paris(self.paris_path)
            pcm, nsamples = synthesize_pcm16(rows, freq, sr, vol, ramp)
            write_wav(out_path, pcm, sr)
            self.log(f"WAV written: {out_path} ({nsamples/sr:.2f}s)")
            return True
        except Exception as e:
            messagebox.showerror("Synthesis failed", f"Could not synthesize audio:\n{e}")
            self.log(f"Error: {e}")
            return False

    def on_play(self):
        if self.player_thread is not None:
            return
        # synthesize to temp wav
        try:
            tmpdir = tempfile.mkdtemp(prefix="parisplay_")
            self.tmp_wav = os.path.join(tmpdir, "preview.wav")
            ok = self.synthesize_to_wav(self.tmp_wav)
            if not ok:
                shutil.rmtree(tmpdir, ignore_errors=True)
                self.tmp_wav = None
                return
            # start player thread
            self.player_thread = PlayerThread(self.tmp_wav)
            self.player_thread.start()
            self.btn_play['state'] = 'disabled'
            self.btn_stop['state'] = 'normal'
            self.log("Playing...")
            # watcher thread to re-enable buttons when done
            threading.Thread(target=self._wait_end, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Play failed", f"Playback error:\n{e}")
            self.log(f"Error: {e}")

    def _wait_end(self):
        # Poll thread; when finished, cleanup temp and reset buttons
        while self.player_thread and self.player_thread.is_alive():
            self.after(200, lambda: None)
            # Simple sleep
            import time
            time.sleep(0.2)
        self.after(0, self._play_done)

    def _play_done(self):
        self.btn_play['state'] = 'normal'
        self.btn_stop['state'] = 'disabled'
        self.log("Playback finished.")
        self.cleanup_tmp()
        self.player_thread = None

    def on_stop(self):
        if self.player_thread:
            self.player_thread.stop()
            self.log("Stop requested.")
        # cleaning happens in watcher

    def cleanup_tmp(self):
        if self.tmp_wav:
            try:
                d = os.path.dirname(self.tmp_wav)
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
            self.tmp_wav = None

    def on_export_wav(self):
        if not self.paris_path:
            messagebox.showwarning("No file", "Open a .paris file first.")
            return
        base = os.path.splitext(os.path.basename(self.paris_path))[0]
        out = filedialog.asksaveasfilename(
            title="Save WAV",
            defaultextension=".wav",
            initialfile=f"{base}.wav",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")]
        )
        if not out:
            return
        if self.synthesize_to_wav(out):
            messagebox.showinfo("Saved", f"WAV saved:\n{out}")

    def on_export_mp3(self):
        if not HAVE_PYDUB:
            messagebox.showwarning(
                "MP3 not available",
                "MP3 export needs 'pydub' and 'ffmpeg' installed and in PATH.\n\n"
                "Tip:\n  pip install pydub\n"
                "  (and install ffmpeg from your package manager or ffmpeg.org)"
            )
            return
        if not self.paris_path:
            messagebox.showwarning("No file", "Open a .paris file first.")
            return
        base = os.path.splitext(os.path.basename(self.paris_path))[0]
        out = filedialog.asksaveasfilename(
            title="Save MP3",
            defaultextension=".mp3",
            initialfile=f"{base}.mp3",
            filetypes=[("MP3 files", "*.mp3"), ("All files", "*.*")]
        )
        if not out:
            return
        # We export WAV to temp, then convert to MP3
        try:
            tmpdir = tempfile.mkdtemp(prefix="paris2mp3_")
            tmpwav = os.path.join(tmpdir, "temp.wav")
            if not self.synthesize_to_wav(tmpwav):
                shutil.rmtree(tmpdir, ignore_errors=True)
                return
            seg = AudioSegment.from_wav(tmpwav)
            seg.export(out, format="mp3")
            shutil.rmtree(tmpdir, ignore_errors=True)
            self.log(f"MP3 saved: {out}")
            messagebox.showinfo("Saved", f"MP3 saved:\n{out}")
        except Exception as e:
            messagebox.showerror("MP3 export failed", f"Could not export MP3:\n{e}")
            self.log(f"Error: {e}")

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
