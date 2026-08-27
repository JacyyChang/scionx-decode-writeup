% eye_fixed_grid.m -- fixed-grid eye analysis of a confirmed (or candidate)
% frame. NO timing-recovery loop of any kind: it slices on a plain uniform
% grid at a constant sps, exactly as 01/03 do, and shows how open the eye is.
%
% Why this exists (and why the older avg-grid picture was wrong): on
% 2026-08-27 a fixed uniform grid at sps=5 was found to decode BOTH seg017
% (across 67% of the symbol period) AND seg002 (across ~90% of it) with a
% passing CRC and a header byte-identical to REFERENCE_FRAME.md -- no Symbol
% Sync loop involved. For seg002 the loop had actively PREVENTED the decode
% (the 144-combo symbol_sync_sweep found zero passes). So the eye is wide
% open and the timing is essentially constant; the loop was unnecessary
% (seg017) or harmful (seg002). This tool measures that openness directly.
%
% The eye-opening metric used here (no knowledge of the correct bits needed):
%   opening(phase) = min{ y(p) : y(p) >  0 }        (the weakest '1')
%                  - max{ y(p) : y(p) <= 0 }         (the strongest '0')
% over the decision instants p in the frame region. binary_slicer_fb decides
% on sign, so threshold = 0; a POSITIVE opening means every decision sits on
% the correct side of the threshold with that much margin to spare -> zero
% bit errors at the decision instant. seg017 measures +5.04 here. This
% REPLACES the earlier mean(|y-threshold|) "margin" -- that averaged over all
% 2200 symbols, ~1880 of which are constant zero-padding far from threshold,
% so it was dominated by the padding and reported a near-flat "barely open"
% curve for an eye that is in fact wide open.
%
% This tool does NOT run the HDLC deframer / CRC (that lives in Python --
% see the fixed-grid decode-window numbers in symbol_sync_sweep.py's README
% section). It shows the eye; Python confirms the decode.
%
% Presets (from the Python fixed-grid decode on 2026-08-27). FRAME_START is
% the sample index of the first decision instant and ALREADY INCLUDES the
% grid phase that decoded the frame, so PHASE below is a *tweak* on top of it
% and defaults to 0 (0 reproduces the confirmed decode):
%   seg017: FRAME_START 916413, N_SYMBOLS 2202   (decoded at grid phase 3.0)
%   seg002: FRAME_START 1159245, N_SYMBOLS 2199  (decoded at grid phase 0.2)
% Switch by editing the block below. Default = seg017.

WAV         = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output\20260723_091639_seg010_300-330s.wav';
FRAME_START = 317088;    % header_correlate_locate.m: header peak 316927 + calibrated
                          % flags->content offset 160.5 samples (see that script)
N_SYMBOLS   = 2200;
SPS         = 5.0;       % <-- fixed grid spacing (48000/9600); constant, no loop
PHASE       = 0.0;       % <-- phase TWEAK on top of FRAME_START; 0 = confirmed decode
THRESH      = 0.0;       % binary_slicer_fb slices on sign
POLARITY    = +1;        % <-- +1 = normal, -1 = inverted (FM-demod polarity can flip;
                          %     the best candidate for a given segment may only appear
                          %     under one polarity -- see symbol_sync_sweep.py's
                          %     norm/inv handling for the same reason)
DECODE_LABEL = 'header-correlation-located (z=13.7, stronger than seg017/002!) -- eye still closed';
% ^ set this by hand per run -- it is NOT inferred from the eye-opening
%   number below, because a positive opening on an arbitrary region does not
%   by itself mean the HDLC flags line up and the CRC passes. Use e.g.
%   'best available candidate region -- NOT decoded' for seg007/seg010,
%   where no CRC-passing frame exists and FRAME_START/N_SYMBOLS instead mark
%   whatever region the flag scan found as closest to a real frame.

% Header span (4 flags + 14-byte address, from header_correlate_locate.m).
% Set HEADER_START = NaN to skip drawing it. HEADER_LEN is always 144
% bits * SPS = 720 samples for this template, regardless of segment.
HEADER_START = 316927;
HEADER_LEN   = 720;

%% ---- load ----
[y, fs] = audioread(WAV);
y = y(:, 1) * POLARITY;
t_idx = (0:numel(y)-1)';                 % 0-based, to match Python indexing

% fractional sample positions -> interpolate, never round (rounding would
% inject up to half a sample of phase error, the very thing being measured)
sample_at = @(pos) interp1(t_idx, y, pos, 'linear', NaN);

n     = (0:N_SYMBOLS-1)';
picks = FRAME_START + PHASE + SPS * n;
vals  = sample_at(picks);

%% ---- eye opening as a function of sampling phase ----
% opening = weakest '1' minus strongest '0' at the decision instants.
ph_grid = linspace(0, SPS, 251);
opening = nan(size(ph_grid));
for i = 1:numel(ph_grid)
    v  = sample_at(FRAME_START + ph_grid(i) + SPS * n);
    hi = v(v >  THRESH);
    lo = v(v <= THRESH);
    if ~isempty(hi) && ~isempty(lo)
        opening(i) = min(hi) - max(lo);   % >0 => open eye, zero decision errors
    end
end
[best_open, i_best] = max(opening);
best_phase = ph_grid(i_best);

hi = vals(vals >  THRESH);
lo = vals(vals <= THRESH);
cur_open = min(hi) - max(lo);

% width of the phase band that keeps the eye open (opening > 0)
open_band = ph_grid(opening > 0);
if isempty(open_band)
    band_str = 'none (eye never fully open on this region)';
else
    band_str = sprintf('%.2f .. %.2f samples (%.0f%% of the symbol period)', ...
        open_band(1), open_band(end), 100*(open_band(end)-open_band(1))/SPS);
end

fprintf('sps = %.4f, phase tweak = %.2f samples (0 = confirmed decode grid)\n', SPS, PHASE);
fprintf('  decision samples: %d ones, %d zeros\n', numel(hi), numel(lo));
fprintf('  weakest 1 = %+.2f,  strongest 0 = %+.2f\n', min(hi), max(lo));
fprintf('  eye opening @ current phase : %+.2f\n', cur_open);
fprintf('  eye opening @ best phase    : %+.2f  (phase %.2f)\n', best_open, best_phase);
fprintf('  eye-open phase band         : %s\n', band_str);
fprintf('  (note: this is the eye''s openness on a FIXED symbol region; it is\n');
fprintf('   not the CRC decode window -- that also needs flag alignment and is\n');
fprintf('   measured in Python. Here it just says how phase-robust the eye is.)\n');

%% ---- eye diagram over the frame ----
n_eye = 251;
tau   = linspace(-SPS, SPS, n_eye);          % +/- one symbol either side
eye   = sample_at(picks + tau);              % N_SYMBOLS x n_eye (implicit expansion)
% draw all traces as ONE line via NaN separators (2200 plot() calls would crawl)
Xe = [repmat(tau, N_SYMBOLS, 1), nan(N_SYMBOLS,1)]';
Ye = [eye,                       nan(N_SYMBOLS,1)]';

%% ---- plot ----
[~, wav_stem_for_title] = fileparts(WAV);
figure('Name', 'fixed-grid eye -- no timing loop', 'Position', [60 60 1500 900]);

subplot(2,2,1);
plot(Xe(:), Ye(:), 'Color', [0 0.447 0.741 0.05]); hold on;
yline(THRESH, 'k--', 'LineWidth', 1);
xline(0,          'Color', [0.85 0.325 0.098], 'LineWidth', 2);
xline(best_phase-PHASE, 'Color', [0.466 0.674 0.188], 'LineWidth', 2, 'LineStyle', ':');
grid on; xlabel('offset from decision instant (samples)'); ylabel('amplitude');
title(sprintf('eye diagram (sps=%.3f) -- orange = current phase, green = best', SPS));

subplot(2,2,2);
plot(ph_grid, opening, 'Color', [0 0.447 0.741], 'LineWidth', 1.5); hold on;
yline(0, 'k--');
plot(PHASE,      cur_open,  'o', 'Color', [0.85 0.325 0.098], ...
    'MarkerFaceColor', [0.85 0.325 0.098], 'MarkerSize', 9);
plot(best_phase, best_open, 'p', 'Color', [0.466 0.674 0.188], ...
    'MarkerFaceColor', [0.466 0.674 0.188], 'MarkerSize', 14);
grid on; xlabel('phase tweak from confirmed grid (samples)'); ylabel('eye opening (weakest 1 - strongest 0)');
legend({'opening vs phase', 'threshold (0)', 'current', 'best'}, 'Location', 'south');
title('eye opening vs phase tweak -- positive = every decision on the correct side');

subplot(2,2,3);
histogram(lo, 40, 'FaceColor', [0.85 0.325 0.098]); hold on;
histogram(hi, 40, 'FaceColor', [0 0.447 0.741]);
xline(THRESH, 'k--', 'LineWidth', 1);
grid on; xlabel('sampled amplitude'); ylabel('count');
legend({'sliced as 0', 'sliced as 1'}, 'Location', 'north');
title('decision-sample histogram -- two clean clusters = open eye');

subplot(2,2,4);
lo_s = max(1, floor(picks(1)) - 5);
hi_s = min(numel(y), ceil(picks(end)) + 5);
if ~isnan(HEADER_START)
    lo_s = max(1, min(lo_s, floor(HEADER_START) - 20));   % widen left edge to include the header
end
tt = (lo_s:hi_s)' - 1;
plot(tt, y(lo_s:hi_s), 'Color', [0.5 0.5 0.5], 'LineWidth', 0.4); hold on;
if ~isnan(HEADER_START)
    yl = [min(y(lo_s:hi_s)), max(y(lo_s:hi_s))];
    patch([HEADER_START, HEADER_START+HEADER_LEN, HEADER_START+HEADER_LEN, HEADER_START], ...
          [yl(1) yl(1) yl(2) yl(2)], [0.9290 0.6940 0.1250], ...
          'FaceAlpha', 0.20, 'EdgeColor', 'none');
    xline(HEADER_START,              'Color', [0.6940 0.4940 0.1250], 'LineWidth', 1.5);
    xline(HEADER_START+HEADER_LEN-1, 'Color', [0.6940 0.4940 0.1250], 'LineWidth', 1.5, 'LineStyle', '--');
end
plot(picks, vals, 'o', 'Color', [0 0.447 0.741], ...
    'MarkerFaceColor', [0 0.447 0.741], 'MarkerSize', 3);
yline(THRESH, 'k--');
grid on; xlabel('sample index'); ylabel('amplitude');
if ~isnan(HEADER_START)
    title('decision points + header span (shaded: 4 flags + 14-byte address) -- zoom in to inspect');
else
    title('decision points on the waveform -- zoom in to inspect');
end

zoom on;

%% ---- overall title: which file/region this is ----
% Three short lines instead of one long one -- a single-line sgtitle at a
% readable font size runs off the figure width for these filenames.
title_lines = {
    wav_stem_for_title
    sprintf('fixed grid, no timing loop: sps=%.3f, region=%d symbols, sample %d..%d (phase tweak %.2f)', ...
        SPS, N_SYMBOLS, FRAME_START, round(FRAME_START + SPS*(N_SYMBOLS-1)), PHASE)
    DECODE_LABEL
};
sgtitle(title_lines, 'Interpreter', 'none', 'FontSize', 12, 'FontWeight', 'bold');

%% ---- save all four panels as a .fig (keeps zoom/pan interactivity) ----
fig_path = fullfile('D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output', ...
    sprintf('eye_fixed_grid_%s.fig', wav_stem_for_title));
savefig(gcf, fig_path);
fprintf('saved figure -> %s\n', fig_path);
