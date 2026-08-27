% power_dip_locate.m -- locate a packet's position inside a 30s segment via
% RMS POWER DIP, instead of trusting "the longest candidate the HDLC flag
% scanner found" -- for seg010/seg007 that scan just latched onto noise
% (eye_fixed_grid.m's histograms showed a single unimodal blob there, not
% the two-cluster structure a real signal gives).
%
% Method, same as 01a_waveform_power_overview.py / 01b (see
% 01_frame_detection/README.md): a real GFSK frame shows up as a power DIP
% under the carrier -- FM quieting suppresses receiver noise once a real
% carrier is present -- not a rise. This script:
%   1. Splits the waveform into WINDOW_MS blocks, computes RMS in dB
%      (same block size as view_segment.m's convention).
%   2. Runs a MATCHED FILTER: a moving average over one frame-duration
%      window, i.e. convolve the per-block power with a boxcar the width of
%      a real frame. This is what turns "the single deepest 20ms block"
%      (noisy, easily fooled) into "the deepest FRAME-DURATION-wide region"
%      (matches 01b's rationale for using a matched filter instead of a
%      naive per-block threshold).
%   3. Reports the sample index of the global minimum of the smoothed curve
%      -- that is the candidate packet location.
%
% VALIDATION FIRST: seg017 and seg002 already have a confirmed, independent
% ground truth (their CRC-passing decode location, found in Python via the
% fixed-grid HDLC search). This script checks the power-dip method against
% both BEFORE trusting it on seg010/seg007. If the two disagree, the dip
% method should not be trusted blindly -- fall back to the header
% cross-correlation approach in 01_frame_detection/01_frame_detection.py
% instead (matched template of the known flag+header bit pattern, not just
% a power feature).
%
% FRAME_DUR_S is set from the two confirmed decodes' own span (destuffed
% frame only, flag-to-flag -- the real on-air frame including its
% surrounding flags/padding is somewhat longer, so this is a lower bound on
% the dip width, used only to size the matched-filter window).

OUT_DIR = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output\';
WINDOW_MS = 20;              % RMS block size, matches view_segment.m
FS_EXPECT = 48000;

% Known-good decode spans (samples), from the Python fixed-grid HDLC search:
%   seg017: FRAME_START=916413, N_SYMBOLS=2202, sps=5 -> span 11010 samples
%   seg002: FRAME_START=1159245, N_SYMBOLS=2199, sps=5 -> span 10995 samples
FRAME_DUR_S = mean([11010, 10995]) / FS_EXPECT;   % ~0.229 s

segments = struct( ...
    'name',  {'seg017 (known-good)', 'seg002 (known-good)', 'seg010', 'seg007'}, ...
    'file',  {'20260723_091639_seg017_510-540s.wav', ...
              '20260723_091639_seg002_60-90s.wav', ...
              '20260723_091639_seg010_300-330s.wav', ...
              '20260720_220206_seg007_210-240s.wav'}, ...
    'known_start', {916413, 1159245, NaN, NaN});   % NaN = no ground truth

figure('Name', 'power-dip packet location', 'Position', [60 60 1500 900]);

for k = 1:numel(segments)
    seg = segments(k);
    [y, fs] = audioread([OUT_DIR seg.file]);
    y = y(:, 1);
    if fs ~= FS_EXPECT
        warning('%s: fs=%d, expected %d', seg.file, fs, FS_EXPECT);
    end

    win = max(1, round(fs * WINDOW_MS / 1000));
    n_blocks = floor(numel(y) / win);
    blocks = reshape(y(1:n_blocks*win), win, n_blocks);
    rms_db = 20*log10(max(sqrt(mean(blocks.^2, 1)), 1e-12));
    t_block = ((0:n_blocks-1) + 0.5) * win / fs;         % block center time (s)

    % matched filter: moving average over one frame duration
    mf_len = max(1, round(FRAME_DUR_S * 1000 / WINDOW_MS));   % in blocks
    mf = movmean(rms_db, mf_len);

    [~, i_dip] = min(mf);
    dip_time   = t_block(i_dip);
    dip_sample = round(dip_time * fs);

    % The matched filter's minimum sits at the CENTER of the boxcar window,
    % i.e. the center of the whole dip (flags + destuffed content + any
    % surrounding padding) -- not the content's start. Both validation
    % cases below land ~0.5 frame-widths late for exactly this reason, so
    % correct for it: shift back by half the matched-filter window.
    half_frame_samples = round(FRAME_DUR_S * fs / 2);
    cand_sample = dip_sample - half_frame_samples;

    fprintf('%-22s dip-center sample=%d -> half-frame-corrected candidate=%d', ...
        seg.name, dip_sample, cand_sample);
    if ~isnan(seg.known_start)
        err_samples = cand_sample - seg.known_start;
        fprintf('   | known-good start=%d  error=%+d samples (%+.1f%% of frame width)\n', ...
            seg.known_start, err_samples, 100*err_samples / (FRAME_DUR_S*fs));
    else
        fprintf('   | no ground truth -- this IS the new candidate location\n');
    end

    subplot(2, 2, k);
    plot(t_block, rms_db, 'Color', [0.7 0.7 0.7], 'LineWidth', 0.6); hold on;
    plot(t_block, mf, 'Color', [0 0.447 0.741], 'LineWidth', 1.8);
    xline(dip_time, 'Color', [0.929 0.694 0.125], 'LineWidth', 1, 'LineStyle', '--');
    xline(cand_sample/fs, 'Color', [0.85 0.325 0.098], 'LineWidth', 2);
    if ~isnan(seg.known_start)
        xline(seg.known_start/fs, 'Color', [0.466 0.674 0.188], 'LineWidth', 2, 'LineStyle', ':');
        legend({'raw RMS (dB)', sprintf('matched filter (%.0fms window)', FRAME_DUR_S*1000), ...
                'dip center (raw)', 'candidate (half-frame corrected)', 'known-good (ground truth)'}, ...
               'Location', 'best', 'FontSize', 7);
    else
        legend({'raw RMS (dB)', sprintf('matched filter (%.0fms window)', FRAME_DUR_S*1000), ...
                'dip center (raw)', 'candidate (half-frame corrected)'}, 'Location', 'best', 'FontSize', 7);
    end
    grid on; xlabel('time (s)'); ylabel('power (dB RMS)');
    title(seg.name, 'Interpreter', 'none');
end

sgtitle({'Power-dip packet location (matched filter over RMS-dB blocks)', ...
         'Validate on seg017/seg002 (known-good) before trusting seg010/seg007'}, ...
        'FontWeight', 'bold', 'FontSize', 12);

fig_path = [OUT_DIR 'power_dip_locate.fig'];
savefig(gcf, fig_path);
fprintf('\nsaved figure -> %s\n', fig_path);
