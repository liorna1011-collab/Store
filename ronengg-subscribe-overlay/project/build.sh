#!/usr/bin/env bash
# Rebuild every deliverable from the source artwork.
#   requirements: python3 (numpy, opencv-python-headless, scipy, pillow), ffmpeg >= 6
#   usage: ./build.sh [portrait|landscape|all]   (default: all)
set -euo pipefail
cd "$(dirname "$0")"
WHAT="${1:-all}"
mkdir -p build

[ -f layers/meta.json ] || python3 extract.py          # 1. decompose artwork into layers
python3 audio.py                                       # 2. sound design (48 kHz)
ffmpeg -v error -y -i build/mix_f32.wav -c:a pcm_s24le build/RonenGG_Subscribe_Audio_48k_24bit.wav

encode () {  # $1 frames dir, $2 output mov
  ffmpeg -v error -y -framerate 60 -i "$1/f_%04d.png" -i build/RonenGG_Subscribe_Audio_48k_24bit.wav \
    -map 0:v -map 1:a \
    -vf "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int+bitexact,format=yuva444p10le" \
    -c:v prores_ks -profile:v 4444 -alpha_bits 8 -qscale:v 4 -vendor apl0 \
    -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv \
    -c:a pcm_s16le -ar 48000 -ac 2 -t 5 -movflags +write_colr \
    -metadata title="RonenGG Daily - Subscribe Overlay" "$2"
}

preview () {  # $1 mov, $2 size WxH, $3 output mp4
  ffmpeg -v error -y -f lavfi -i "gradients=s=$2:r=60:d=5:n=5:c0=0x101a3a:c1=0x2d6fb0:c2=0xf2b36b:c3=0x7a2e8f:c4=0x0d4f4a:speed=0.035:type=radial:seed=4" \
    -i "$1" -filter_complex "[0:v]noise=alls=3:allf=t,vignette=PI/5,format=yuv444p[bg];[1:v]format=yuva444p10le[ov];[bg][ov]overlay=0:0:format=yuv444:alpha=straight,format=yuv420p[v]" \
    -map "[v]" -map 1:a -c:v libx264 -preset slow -crf 19 -profile:v high \
    -colorspace bt709 -color_primaries bt709 -color_trc bt709 -movflags +faststart -c:a aac -b:a 256k -t 5 "$3"
}

if [ "$WHAT" = portrait ] || [ "$WHAT" = all ]; then
  python3 render.py --layout portrait --out build/frames_portrait --jobs 4
  encode build/frames_portrait build/RonenGG_Subscribe_Overlay_1080x1920_60fps_ProRes4444.mov
  preview build/RonenGG_Subscribe_Overlay_1080x1920_60fps_ProRes4444.mov 1080x1920 build/RonenGG_Subscribe_Preview_over_sample_video_1080x1920.mp4
  python3 render.py --layout portrait --still 3.6 --still_out build/RonenGG_Subscribe_Still_Subscribed_1080x1920.png
fi
if [ "$WHAT" = landscape ] || [ "$WHAT" = all ]; then
  python3 render.py --layout landscape --out build/frames_landscape --jobs 4
  encode build/frames_landscape build/RonenGG_Subscribe_Overlay_1920x1080_60fps_ProRes4444.mov
  preview build/RonenGG_Subscribe_Overlay_1920x1080_60fps_ProRes4444.mov 1920x1080 build/RonenGG_Subscribe_Preview_over_sample_video_1920x1080.mp4
fi
python3 render.py --layout still_hires --still 3.6 --still_out build/RonenGG_Subscribe_Still_Subscribed_HiRes.png
python3 render.py --layout still_hires --still 1.2 --still_out build/RonenGG_Subscribe_Still_Original_HiRes.png
echo done
