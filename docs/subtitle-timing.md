# Subtitle timing

In Clip Studio, choose the clip to preview, then click **Sinkronkan dari audio**.
The backend transcribes that clip with Whisper and applies its word timestamps
to both the live preview and subsequent server renders. This replaces the text
and timings for that clip. Other selected clips keep their existing settings.

You can also synchronize inside **Edit Transcript**. Changes there stay in the
editor until **Apply Transcript** is clicked. Correcting a sentence with the same
number of words preserves the existing word timestamps. Changing the word count
estimates that sentence's timing again and displays a reminder to review it.

Animated captions preserve acoustic word starts and ends, including fast speech
and pauses. Preview uses the same chunk boundaries and centisecond rounding as
the ASS export. Plain captions display a phrase until its final word ends.

If Whisper is unavailable, synchronization reports an error instead of presenting
estimated line timing as acoustic timing. Existing automatic renders can still
fall back to transcript timing; line-only tracks are estimates. Local analysis
now retains Whisper's word timestamps for the editor and fallback renderer.

`WHISPER_MODEL` in the backend environment selects the transcription model
(default `base`). `small` uses a larger model and needs more memory and processing
time. The first use downloads a model if it is not already cached. Restart the
backend after changing this setting. Recognition quality still depends on the
recording; check a real clip with difficult speech, music, or overlapping voices.
