import threading
import time
import csv
import os
import subprocess
import platform
from collections import deque
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, StringVar, IntVar, BooleanVar

from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from webdriver_manager.firefox import GeckoDriverManager

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Constants
DEFAULT_POLL_INTERVAL = 10  # seconds
MAX_POINTS = 100  # number of points to keep in plot

def open_file(path):
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
    except Exception as e:
        print(f"Failed to open file: {e}")

class StreamMonitorGUI:
    def __init__(self, root):
        self.root = root
        root.title("Twitch Viewer Monitor")

        self.channel_var = StringVar()
        self.poll_interval_var = IntVar(value=DEFAULT_POLL_INTERVAL)
        self.headless_var = BooleanVar(value=True)

        self.viewer_count_var = StringVar(value="N/A")
        self.uptime_var = StringVar(value="N/A")
        self.script_uptime_var = StringVar(value="N/A")
        self.percent_change_var = StringVar(value="N/A")
        self.status_var = StringVar(value="Idle")

        self.monitor_thread = None
        self.stop_event = threading.Event()

        self.previous_viewers = None
        self.start_time = None
        self.csv_file = None

        self._build_ui()
        self.viewer_history = deque(maxlen=500)
        self.time_history = deque(maxlen=500)

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")

        # Input row
        input_frame = ttk.LabelFrame(frm, text="Configuration", padding=8)
        input_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5, columnspan=2)
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="Channel:").grid(row=0, column=0, sticky="w")
        self.channel_entry = ttk.Entry(input_frame, textvariable=self.channel_var, width=25)
        self.channel_entry.grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(input_frame, text="Poll interval (s):").grid(row=0, column=2, sticky="w", padx=(10,0))
        self.poll_spin = ttk.Spinbox(input_frame, from_=5, to=300, textvariable=self.poll_interval_var, width=5)
        self.poll_spin.grid(row=0, column=3, sticky="w", padx=4)

        self.headless_check = ttk.Checkbutton(input_frame, text="Headless", variable=self.headless_var)
        self.headless_check.grid(row=0, column=4, sticky="w", padx=(10,0))

        # Status / metrics
        metrics_frame = ttk.LabelFrame(frm, text="Live Data", padding=8)
        metrics_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        metrics_frame.columnconfigure(1, weight=1)

        ttk.Label(metrics_frame, text="Viewer count:").grid(row=0, column=0, sticky="w")
        ttk.Label(metrics_frame, textvariable=self.viewer_count_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=1, sticky="w")

        ttk.Label(metrics_frame, text="Stream uptime:").grid(row=1, column=0, sticky="w")
        ttk.Label(metrics_frame, textvariable=self.uptime_var).grid(row=1, column=1, sticky="w")

        ttk.Label(metrics_frame, text="Script uptime:").grid(row=2, column=0, sticky="w")
        ttk.Label(metrics_frame, textvariable=self.script_uptime_var).grid(row=2, column=1, sticky="w")

        ttk.Label(metrics_frame, text="Percent change:").grid(row=3, column=0, sticky="w")
        ttk.Label(metrics_frame, textvariable=self.percent_change_var).grid(row=3, column=1, sticky="w")

        ttk.Label(metrics_frame, text="Status:").grid(row=4, column=0, sticky="w")
        ttk.Label(metrics_frame, textvariable=self.status_var).grid(row=4, column=1, sticky="w")

        # Controls
        controls = ttk.Frame(frm)
        controls.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.start_btn = ttk.Button(controls, text="Start", command=self.start_monitor)
        self.start_btn.grid(row=0, column=0, padx=2)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.stop_monitor, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=2)
        self.open_csv_btn = ttk.Button(controls, text="Open CSV", command=self.open_csv, state="disabled")
        self.open_csv_btn.grid(row=0, column=2, padx=2)

        # Plot area
        plot_frame = ttk.LabelFrame(frm, text="Viewer Count Over Time", padding=8)
        plot_frame.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=5, pady=5)
        plot_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)
        self.fig = Figure(figsize=(5,3), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Viewers")
        self.ax.set_xlabel("Elapsed (s)")
        self.ax.set_ylabel("Viewer Count")
        self.line, = self.ax.plot([], [])
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Log / output
        log_frame = ttk.LabelFrame(frm, text="Log", padding=8)
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=10, state="disabled", wrap="word")
        self.log_widget.grid(row=0, column=0, sticky="nsew")

        # Make window resize nicely
        self.root.columnconfigure(0, weight=1)
        frm.rowconfigure(3, weight=1)
        frm.columnconfigure(1, weight=1)

        # On close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full = f"[{timestamp}] {msg}\n"
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", full)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def initialize_csv(self, filename):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        if not os.path.exists(filename):
            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['uptime', 'viewer_count', 'percentage_change'])

    def get_csv_filename(self, channel):
        current_date = datetime.now().strftime("%Y-%m-%d")
        return os.path.join("viewer_data", f"{channel}_viewer_scrape_{current_date}.csv")

    def get_viewer_count(self, driver):
        try:
            element = driver.find_element(By.XPATH, '//span[contains(@class, "ScAnimatedNumber") and contains(@class, "jAIlLI")]')
            viewer_text = element.text.replace(',', '')
            return int(viewer_text)
        except NoSuchElementException:
            return None
        except ValueError:
            return None

    def get_stream_time(self, driver):
        try:
            element = driver.find_element(By.XPATH, '//span[contains(@class, "live-time")]//span[@aria-hidden="true"]')
            time_text = element.text  # e.g., "8:10:11"
            return time_text
        except NoSuchElementException:
            return None

    def start_monitor(self):
        channel = self.channel_var.get().strip()
        if not channel:
            messagebox.showerror("Error", "Please provide a twitch channel name.")
            return

        if self.monitor_thread and self.monitor_thread.is_alive():
            return  # already running

        self.stop_event.clear()
        self.previous_viewers = None
        self.viewer_history.clear()
        self.time_history.clear()
        self.start_time = time.time()
        self.csv_file = self.get_csv_filename(channel)
        self.initialize_csv(self.csv_file)

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.open_csv_btn.config(state="normal")
        self.channel_entry.config(state="disabled")

        self.status_var.set("Starting...")
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop_monitor(self):
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.stop_btn.config(state="disabled")
        self.start_btn.config(state="normal")
        self.channel_entry.config(state="normal")
        self.status_var.set("Stopped")

    def open_csv(self):
        if self.csv_file and os.path.exists(self.csv_file):
            open_file(self.csv_file)
        else:
            messagebox.showinfo("Info", "CSV file does not exist yet.")

    def _update_plot(self):
        if not self.time_history:
            return
        # x axis: seconds since start
        elapsed_secs = [t for t in self.time_history]
        viewers = [v for v in self.viewer_history]
        self.line.set_data(elapsed_secs, viewers)
        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()

    def _monitor_loop(self):
        channel = self.channel_var.get().strip()
        url = f"https://www.twitch.tv/{channel}"
        loading_time = 10

        options = FirefoxOptions()
        if self.headless_var.get():
            options.add_argument("--headless")

        try:
            service = Service(GeckoDriverManager().install())
            driver = webdriver.Firefox(service=service, options=options)
        except Exception as e:
            self.log(f"Failed to start browser: {e}")
            self.status_var.set("Browser start failed")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.channel_entry.config(state="normal")
            return

        try:
            self.log(f"Navigating to {url}")
            driver.get(url)
            time.sleep(loading_time)
        except Exception as e:
            self.log(f"Failed to load page: {e}")
            self.status_var.set("Load error")
            driver.quit()
            return

        start_time = time.time()
        previous_viewers = None
        percent_change = 0
        err_check = 0

        while not self.stop_event.is_set():
            try:
                if err_check > 4:
                    self.log("Viewer count could not be found after repeated attempts. "
                             "Stream is likely offline or layout changed. Stopping.")
                    self.status_var.set("Element not found")
                    break

                uptime = self.get_stream_time(driver) or "N/A"
                viewers = self.get_viewer_count(driver)

                elapsed_time = time.time() - start_time
                formatted_uptime = str(timedelta(seconds=int(elapsed_time)))

                # Script uptime from initial start of GUI monitoring
                total_script_elapsed = time.time() - self.start_time
                formatted_script_uptime = str(timedelta(seconds=int(total_script_elapsed)))

                if viewers is not None:
                    err_check = 0
                    if previous_viewers is not None:
                        try:
                            percent_change = ((viewers - previous_viewers) / previous_viewers) * 100
                            change_str = f"{percent_change:+.2f}%"
                        except ZeroDivisionError:
                            change_str = "N/A"
                    else:
                        change_str = "N/A"

                    self.viewer_count_var.set(str(viewers))
                    self.uptime_var.set(uptime)
                    self.script_uptime_var.set(formatted_script_uptime)
                    self.percent_change_var.set(change_str)
                    self.status_var.set("Running")

                    log_msg = (f"{channel} [Stream uptime: {uptime} | Script uptime: {formatted_script_uptime}] "
                               f"Viewers: {viewers} ({change_str})")
                    self.log(log_msg)

                    with open(self.csv_file, 'a', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow([uptime, viewers, percent_change])

                    # update history for plotting
                    self.viewer_history.append(viewers)
                    self.time_history.append(int(total_script_elapsed))
                    self.root.after(0, self._update_plot)

                    previous_viewers = viewers
                else:
                    err_check += 1
                    self.log(f"Viewer count not found. Attempt {err_check}/5.")
                    self.status_var.set(f"Retrying ({err_check})")

                # refresh the page occasionally or recover?
                # Could add logic here to refresh if stale

            except Exception as e:
                self.log(f"Error during monitoring loop: {e}")
                self.status_var.set("Error")
            # Sleep respecting poll interval, but break early if stopped
            for _ in range(int(self.poll_interval_var.get())):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        driver.quit()
        self.status_var.set("Stopped")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.channel_entry.config(state="normal")
        self.log("Monitoring stopped.")

    def _on_close(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            if messagebox.askyesno("Quit", "Monitoring is running. Stop and exit?"):
                self.stop_monitor()
                self.root.after(500, self.root.destroy)
            else:
                return
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    app = StreamMonitorGUI(root)
    root.geometry("900x600")
    root.mainloop()

if __name__ == "__main__":
    main()
