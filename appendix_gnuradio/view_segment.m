% view_segment.m -- plot every candidate/confirmed signal segment found so
% far across all captures (waveform + block-wise RMS power in dB per
% segment), with linked-axis zoom per figure.
%
% Power method matches 01_frame_detection/01a_waveform_power_overview.py:
% non-overlapping window_ms blocks, RMS, 20*log10. A real GFSK frame shows up
% as a power DIP (FM quieting suppresses receiver noise under a real carrier),
% not a rise -- look for the RMS panel dropping several dB below its own
% median, not for a peak.
%
% Edit the "candidates" list below to add/remove segments, then just run the
% whole script (F5 or Editor > Run). audioread's [start end] sample-range
% form is used so a long recording isn't decoded in full just to look at a
% few seconds of it.

FIG = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Figure\';
window_ms = 20;   % RMS power block size (ms); 20 matches the project default

%% ---- candidate/confirmed segments found so far -- edit this list ----
candidates = {
    struct('wav', [FIG '20260723_091639_48k.wav'], 'segs', [2, 10, 17], ...
        'note', 'CANDIDATE ONLY -- flagged from the seg###_spec_*.png spectrogram scan, not yet decoded/verified')

    struct('wav', [FIG '20260720_220206_48k.wav'], 'segs', 7, ...
        'note', ['CONFIRMED -- seg007''s frame (sample 182707, z=5.74) decoded via ' ...
                 '04d_destuff_interactive_ver2.py --z-threshold 5.5: only 30/1320 mismatches ' ...
                 'at verifiable (header/padding) bit positions -- real frame, weaker link than cut_first3.ogg'])

    struct('wav', [FIG '20260810_220940_48k.wav'], 'segs', [4, 18], ...
        'note', 'CANDIDATE ONLY -- flagged from the seg###_spec_*.png spectrogram scan, not yet decoded/verified')
};
interval_s = 30;   % seconds/segment -- matches start_periodic_plot(30) in IQtoOgg_plot.grc, same for all captures above

%% ---- one figure per segment ----
for c = 1:numel(candidates)
    wav_path = candidates{c}.wav;
    info = audioinfo(wav_path);
    fs = info.SampleRate;
    [~, wav_name] = fileparts(wav_path);
    fprintf('\n=== %s -- %s ===\n', wav_name, candidates{c}.note);

    for seg_idx = candidates{c}.segs
        t_start = seg_idx * interval_s;
        t_end   = min((seg_idx+1) * interval_s, info.Duration);
        samp_range = [floor(t_start*fs)+1, floor(t_end*fs)];
        [y, fs] = audioread(wav_path, samp_range);
        t = t_start + (0:numel(y)-1)/fs;

        win = max(1, round(fs*window_ms/1000));
        n_blocks = floor(numel(y)/win);
        blocks = reshape(y(1:n_blocks*win), win, n_blocks);
        rms_lin = sqrt(mean(blocks.^2, 1));
        rms_db = 20*log10(max(rms_lin, 1e-12));
        t_pow = t_start + ((0:n_blocks-1) + 0.5) * win / fs;

        figure('Name', sprintf('%s -- seg%03d [%.0f-%.0fs]', wav_name, seg_idx, t_start, t_end));

        ax1 = subplot(2,1,1);
        plot(t, y, 'Color', [0 0.447 0.741]);
        ylabel('amplitude'); grid on;
        title(sprintf('%s seg%03d: waveform [%.0f-%.0fs]', wav_name, seg_idx, t_start, t_end), 'Interpreter', 'none');

        ax2 = subplot(2,1,2);
        plot(t_pow, rms_db, 'Color', [0.85 0.325 0.098]);
        xlabel('time (s)'); ylabel('power (dB, RMS)'); grid on;
        title(sprintf('RMS power envelope (%dms blocks) -- look for power DIPS (FM quieting)', window_ms));

        linkaxes([ax1, ax2], 'x');
        xlim(ax1, [t(1) t(end)]);

        fprintf('  seg%03d: samples %d-%d (%.0fs-%.0fs), n=%d\n', seg_idx, samp_range(1), samp_range(2), t_start, t_end, numel(y));
        fprintf('    rms_db range: [%.1f, %.1f] dB, median: %.1f dB\n', min(rms_db), max(rms_db), median(rms_db));
    end
end
