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
4. Install dependencies (`pip install -U -r requirements.txt`) (**if updating builder, you will need to run this again**)
5. Run the command `python main.py` (double-clicking `main.py` file might also work)

## Charts
Each difficulty can be a `.sus` file, a `.usc` file, or a Next SEKAI / Next RUSH or Chart Cyanvas LevelData file (`.json`, `.json.gz`, `.nsld` or `.gz`, plain or gzipped JSON; which kind is told from its content). Every chart is converted to Next SEKAI LevelData when it is compiled.

If a LevelData chart uses features the converter does not support and unsupported parts are then skipped or replaced with the closest supported equivalent (usually skipped).

The game does not use the chart's own offset (SUS `#WAVEOFFSET`, USC `offset`, LevelData `bgmOffset`). Set the timing with `offset_ms` instead before compiling. NOTE: `offset_ms` is prefilled when downloading from UntitledCharts or the CC Archive.

## Import from UntitledCharts
`Import from UntitledCharts...` starts a new project from a level on [untitledcharts.com](https://untitledcharts.com).

The builder downloads the chart, song, preview and cover into `untitledcharts/<level name>/`. A preview longer than 45 seconds (UntitledCharts often uses the whole song - you should cut it yourself in that case!) is cut to its first 45 seconds. Fill in the remaining required fields (Lyricist, Composer, Arranger, Vocals) before building.

## Import from CC Archive
`Import from CC Archive...` does the same for a chart on the Chart Cyanvas archive, [cc.milkbun.org](https://cc.milkbun.org) (a `cc.milkbun.org/charts/...` link). Its chart is Chart Cyanvas LevelData; the files go into `ccarchive/<chart name>/`.

## Compiled and uncompiled files
The chart builder can output a `.zip` or a `.<platform>.chart.gs6` file. Use `Build zip...` button to export `.zip` and `Compile .chart.gs6...` to output these.

Please use `Build zip...` to save a copy of your chart project. You can `Import .zip...` at any time to import that project and edit it. You can also share this `.zip` to other people so they can directly compile or edit your project if necessary.

Please use `Compile .chart.gs6...` to create copies of your chart that can be used in GridlessSekai6's Custom Chart mod. This can take a while. You must compile the files for use. Android `.chart.gs6` files will only work on Android, and iOS `.chart.gs6` will only work on iOS.