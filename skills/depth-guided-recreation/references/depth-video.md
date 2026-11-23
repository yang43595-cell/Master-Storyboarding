# 深度视频工具与核对

## 准备同窗输入

从开工登记和镜号取源文件、时窗及画幅。优先复用与本轮源版本、时窗和用途一致的已核深度片。否则用已就绪的视频深度估计工具抽取。先从原片截出实际范围并保留画幅，跨硬切时按镜头或连续源段处理，交接拍法时保留原片的镜头与时间点。工具输出按实际切片的时间轴核对。源片为变帧率且工具需要恒定帧率时，可先另存规范化输入，记录原片时间窗和规范化方式，同时校对处理后的实际切点与动作时点。

以 FFmpeg 裁出实际输入的调用示例。将时间、路径替换成当前段的值，输出使用新路径。

```sh
ffmpeg -n -i '<原片>' -ss '<起点>' -t '<实际窗长>' -map 0:v:0 -an \
  -c:v libx264 -pix_fmt yuv420p '<镜段输入.mp4>'
```

## 本地抽取选项

已配置的工具按其实际入口运行。可选 [Video Depth Anything Small 官方说明](https://github.com/DepthAnything/Video-Depth-Anything/blob/4f5ae23172ba60fd7bc11ef671cca678842c7072/README.md)，在仓库外使用独立环境，将 [Small 相对深度权重](https://huggingface.co/depth-anything/Video-Depth-Anything-Small/blob/main/video_depth_anything_vits.pth) 放在该工具的 `checkpoints/video_depth_anything_vits.pth`。确认具体版本和权重后，在工具自己的目录运行。安装与权重独立于 Master-Storyboarding 常规环境。

```sh
python run.py --input_video '<镜段输入的绝对路径>' \
  --output_dir '<新输出目录的绝对路径>' --encoder vits --grayscale --fp32
```

用实际生成的 `*_vis.mp4` 作为深度候选，`*_src.mp4` 可帮助确认工具实际处理了哪些源帧。上述版本的 `run.py` 使用 CUDA，CUDA 不可用时使用 CPU。记录工具、模型与权重版本、源文件版本和范围、预处理、实际输出路径及核验结论。依赖或权重缺失时报告缺项，保留可核的镜头与外观资料，待深度就绪后再完成依赖该视频的稿件。

## 检查实际深度片

对镜段输入和深度输出分别运行，读取实际显示画幅、视频起始时间、所有解码帧的时间及帧数。

```sh
ffprobe -v error -select_streams v:0 -show_streams -show_frames \
  -show_entries 'stream=width,height,sample_aspect_ratio,time_base,start_time,duration,avg_frame_rate:stream_side_data=rotation:frame=best_effort_timestamp_time' \
  -of json '<待核文件>'
```

若本机只有可用的 FFmpeg，以下命令可读取实际帧时间和尺寸，比较两份媒体的 `showinfo` 日志。

```sh
ffmpeg -hide_banner -nostats -i '<待核文件>' -map 0:v:0 -an \
  -vf showinfo -fps_mode passthrough -f null -
```

- 对照两条流以首帧为零的相对时间、帧数、尾帧与覆盖长度，分清编码时间基舍入和真实掉帧、重排或速度变化。只看标称时长或帧率会漏掉偏移。
- 核画幅、旋转和取景保持一致，允许保画幅缩放。实际连放，抽看开头、动作发生、停顿、切点和末尾，检查相位、人物轮廓、遮挡顺序和深度闪烁。深度图应呈现前后层次，而单纯把原片转灰度仍是亮度图。
- 处理后实际会送入模型的是独立的结构参考视频。确认它不携带本轮未选的声音，交接时写清源镜号、源窗与深度时间映射。只有输入和用途对应的深度片进入相关镜段，画布保存和样片生成另沿视频 Skill。
