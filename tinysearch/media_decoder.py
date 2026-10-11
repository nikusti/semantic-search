"""Bundled decoders run in short-lived, timeout-bounded subprocesses."""
from fractions import Fraction
from io import BytesIO
import json
import math
from pathlib import Path
import sys


def open_media(path):
    import av
    # File-only protocols prevent playlist files from accessing remote URLs.
    return av.open(str(path), options={'protocol_whitelist': 'file,pipe', 'threads': '1'})


def probe(path):
    import av
    with open_media(path) as source:
        streams = source.streams
        durations = [float(s.duration * s.time_base) for s in streams if s.duration is not None]
        duration = float(source.duration / av.time_base) if source.duration is not None else max(durations, default=0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Media has no finite positive duration')
        def stream_duration(kind):
            stream = next((s for s in streams if s.type == kind), None)
            if stream is None: return None
            value = float(stream.duration * stream.time_base) if stream.duration is not None else duration
            return min(value, duration) if math.isfinite(value) and value > 0 else duration
        video, audio = stream_duration('video'), stream_duration('audio')
        return dict(duration=duration, has_video=video is not None, has_audio=audio is not None,
                    video_duration=video, audio_duration=audio)


def seek(source, stream, seconds):
    offset = int(seconds / stream.time_base) + (stream.start_time or 0)
    source.seek(max(0, offset), stream=stream, backward=True)


def frame_time(frame, stream):
    return float(frame.pts * frame.time_base) - float((stream.start_time or 0) * stream.time_base)


def picture(path, seconds, size, orient=False):
    with open_media(path) as source:
        stream = source.streams.video[0]
        seek(source, stream, seconds)
        last = None
        for frame in source.decode(stream):
            last = frame
            if frame.pts is not None and frame_time(frame, stream) + 1e-6 >= seconds: break
        if last is None: raise ValueError('No video frame available')
        image = last.to_image().convert('RGB')
        if orient and last.rotation:
            image = image.rotate(last.rotation, expand=True)
        image.thumbnail((size, size))
        output = BytesIO()
        image.save(output, 'JPEG', quality=88)
        return output.getvalue()


def audio(path, start, end):
    import av
    import numpy as np
    maximum = math.ceil((end-start)*16000)
    output = np.zeros(maximum, dtype='<f4')
    written = 0
    with open_media(path) as source:
        stream = source.streams.audio[0]
        seek(source, stream, max(0, start-.1))
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=16000)
        def collect(frame):
            nonlocal written
            if frame.pts is None: return
            seconds = float(frame.pts * frame.time_base) - float((stream.start_time or 0) * stream.time_base)
            samples = frame.to_ndarray().reshape(-1)
            position = round((seconds-start)*16000)
            first = max(0, -position)
            position = max(0, position)
            length = min(len(samples)-first, maximum-position)
            if length > 0:
                output[position:position+length] = samples[first:first+length]
                written = max(written, position+length)
        for frame in source.decode(stream):
            seconds = frame_time(frame, stream) if frame.pts is not None else start
            if seconds > end+.1: break
            for resampled in resampler.resample(frame): collect(resampled)
        for resampled in resampler.resample(None): collect(resampled)
    if written < 320 or not np.isfinite(output[:written]).all():
        raise ValueError('No usable audio samples')
    return output[:written]


def segment(path, output, start, end, video, orient=False):
    import av
    import numpy as np
    if not video:
        import wave
        samples = audio(path, start, end)
        with wave.open(str(output), 'wb') as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
            target.writeframes((np.clip(samples, -1, 1)*32767).astype('<i2').tobytes())
        return
    info = probe(path)
    with open_media(path) as source, av.open(str(output), 'w', format='mp4') as target:
        stream = source.streams.video[0]
        seek(source, stream, start)
        encoder = target.add_stream('h264_videotoolbox', rate=30)
        width, height = stream.codec_context.width, stream.codec_context.height
        scale = min(1, 1280/width, 720/height)
        encoder.width = max(2, int(width*scale)//2*2)
        encoder.height = max(2, int(height*scale)//2*2)
        encoder.pix_fmt = 'yuv420p'
        encoder.bit_rate = 2_000_000
        encoder.options = {'allow_sw': '1'}
        sound = target.add_stream('aac', rate=16000) if info['has_audio'] else None
        if sound: sound.layout = 'mono'
        previous = -1
        for frame in source.decode(stream):
            if frame.pts is None: continue
            seconds = frame_time(frame, stream)
            if seconds < start: continue
            if seconds >= end: break
            position = round((seconds-start)*30)
            if position <= previous: continue
            if orient and frame.rotation:
                image = frame.to_image().rotate(frame.rotation, expand=True)
                if previous == -1:
                    scale = min(1, 1280/image.width, 720/image.height)
                    encoder.width = max(2, int(image.width*scale)//2*2)
                    encoder.height = max(2, int(image.height*scale)//2*2)
                frame = av.VideoFrame.from_image(image)
            previous = position
            frame = frame.reformat(encoder.width, encoder.height, 'yuv420p')
            frame.pts, frame.time_base = position, Fraction(1,30)
            for packet in encoder.encode(frame): target.mux(packet)
        for packet in encoder.encode(None): target.mux(packet)
        if sound:
            samples = audio(path, start, end)
            for offset in range(0, len(samples), 1024):
                frame = av.AudioFrame.from_ndarray(samples[offset:offset+1024].reshape(1,-1), format='fltp', layout='mono')
                frame.sample_rate, frame.pts, frame.time_base = 16000, offset, Fraction(1,16000)
                for packet in sound.encode(frame): target.mux(packet)
            for packet in sound.encode(None): target.mux(packet)


def pdf(path):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(path) as document:
        if not len(document): raise ValueError('PDF has no pages')
        page = document[0]
        try:
            width, height = page.get_size()
            bitmap = page.render(scale=1400/max(width,height))
            try:
                image = bitmap.to_pil().convert('RGB')
                output = BytesIO(); image.save(output, 'JPEG', quality=88)
                return output.getvalue()
            finally: bitmap.close()
        finally: page.close()


def main():
    operation, name, *values = sys.argv[1:]
    path = Path(name)
    if not path.is_file(): raise ValueError('Expected a readable local file')
    if operation == 'probe': sys.stdout.write(json.dumps(probe(path)))
    elif operation == 'frame': sys.stdout.buffer.write(picture(path,float(values[0]),int(values[1]),bool(int(values[2])) if len(values)>2 else False))
    elif operation == 'audio': sys.stdout.buffer.write(audio(path,float(values[0]),float(values[1])).tobytes())
    elif operation == 'pdf': sys.stdout.buffer.write(pdf(path))
    elif operation == 'segment': segment(path,Path(values[0]),float(values[1]),float(values[2]),bool(int(values[3])),bool(int(values[4])) if len(values)>4 else False)
    else: raise ValueError('Unknown decoder operation')


if __name__ == '__main__': main()
