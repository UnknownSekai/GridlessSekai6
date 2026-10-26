# How to use the custom chart builder

Before starting, learn how to navigate with Command Prompt / Terminal / Powershell (depending on device).

1. Download the entire `builder/` folder. Everything here is required (including `base/`)
2. Install Python (python.org, recommend 3.11-3.13), and FFmpeg (ffmpeg.org)
3. Ensure Python is in path, and FFmpeg is either in path or its `ffmpeg` and `ffprobe` executables are copied into this `builder/` folder (next to `main.py`):
  ```bash
  python --version
  ffmpeg -version
  ffprobe -version # part of ffmpeg
  ```
4. Install dependencies (`pip install -U -r requirements.txt`)
5. Run the command `python main.py` (double-clicking `main.py` file might also work)

## Charts
Each difficulty can be a `.sus` or a `.usc` file. A `.usc` chart is converted to SUS when it is compiled.

## Compiled and uncompiled files
The chart builder can output a `.zip` or a `.<platform>.chart.gs6` file. Use `Build zip...` button to export `.zip` and `Compile .chart.gs6...` to output these.

Please use `Build zip...` to save a copy of your chart project. You can `Import .zip...` at any time to import that project and edit it. You can also share this `.zip` to other people so they can directly compile or edit your project if necessary.

Please use `Compile .chart.gs6...` to create copies of your chart that can be used in GridlessSekai6's Custom Chart mod. This can take a while. You must compile the files for use. Android `.chart.gs6` files will only work on Android, and iOS `.chart.gs6` will only work on iOS.