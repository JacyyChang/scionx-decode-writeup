% ============================================================================
% SUPERSEDED 2026-08-27 by eye_fixed_grid.m -- its "Symbol Sync avg grid" and
% cumulative-drift figure are built on avg_sps = (whole-file input samples) /
% (output symbols), which was misread as a ~1.5% real clock offset within the
% frame. That reading is RETRACTED: ~29 of the file's 30s are noise, where
% the loop free-runs, so avg_sps describes the loop's behaviour in noise, not
% the frame's symbol rate. Directly measured (eye_fixed_grid.m), sps=5.0 is
% exact and there is no drift within the frame at all. Also: a fixed grid
% alone (no Symbol Sync) turned out to decode seg017 across 67% of the
% symbol period -- see README.md, "Fixed grid alone decodes seg017 and
% seg002". Kept only for its zoomable two-grid comparison view; don't cite
% its drift numbers.
% ============================================================================
%
% plot_pickpoints_seg017.m -- zoomable MATLAB version of the ONE confirmed
% decode's pick-point figure: 20260723_091639 seg017 (510-540s), decoded with
% Symbol Sync MM / loop_bw=0.045 / damping=0.7 / max_dev=1.5.
%
% This is the interactive counterpart to plot_pickpoints_comparison.py's
% --whole PNG. Deliberately minimal: it does NOT run Symbol Sync, detect
% frames, or destuff anything -- the four numbers that describe that decode
% are pasted in below (they are printed by the Python tool), so this file is
% just audioread + plot, and you get real MATLAB axes to zoom/pan on instead
% of a static image. Just run it (F5).
%
% Constants below came from, on 2026-08-26:
%   python plot_pickpoints_comparison.py Output/20260723_091639_seg017_510-540s.wav \
%       --ted MM --loop-bw 0.045 --damping 0.7 --max-dev 1.5 --whole
%   -> Best candidate: polarity=norm  len=274 bytes  bit range=[186582, 188783]
%      (out of 292483 total symbols)
%
% CAVEATS (same as the Python tool -- read before over-reading the markers):
%  1. digital.symbol_sync_ff does not expose its per-symbol timing phase, so
%     the orange "Symbol Sync" grid is a UNIFORM grid at the whole-file
%     AVERAGE sps, not the loop's real instantaneous phase. It shows the
%     average rate the loop settled on; it does NOT show symbol-to-symbol
%     jitter, which real timing recovery also corrects for.
%  2. That average is taken over the whole 30 s file, most of which is noise
%     rather than signal, so the loop is free-running for most of it. Treat
%     avg_sps as indicative of "the loop did not settle at 5.000", NOT as a
%     measurement of this frame's true symbol rate.

WAV        = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output\20260723_091639_seg017_510-540s.wav';
N_OUT_BITS = 292483;    % total symbols symbol_sync_ff produced for this file
BIT_START  = 186582;    % first symbol of the confirmed 274-byte frame
BIT_END    = 188783;    % last symbol of it (inclusive)
SPS_NOM    = 5.0;       % 01/03's fixed grid spacing (48000/9600)

%% ---- load + derive the two grids ----
[y, fs] = audioread(WAV);
y = y(:, 1);

avg_sps      = numel(y) / N_OUT_BITS;      % what the loop actually averaged
n_symbols    = BIT_END - BIT_START;
sample_start = BIT_START * avg_sps;        % same anchor for both grids

n            = (0:n_symbols-1)';
idx_fixed    = sample_start + SPS_NOM * n; % what 01/03 would have sampled
idx_symsync  = sample_start + avg_sps * n; % what Symbol Sync averaged to
drift        = idx_symsync - idx_fixed;    % cumulative divergence, in samples

% MATLAB is 1-based; the Python tool truncates toward zero, so floor()+1 here.
pick = @(idx) y(floor(idx) + 1);

lo = max(1, floor(min(idx_fixed(1), idx_symsync(1))) - 5);
hi = min(numel(y), ceil(max(idx_fixed(end), idx_symsync(end))) + 5);
t  = (lo:hi)';

fprintf('avg_sps = %.5f (nominal %.3f) -> %+.5f samples/symbol\n', ...
    avg_sps, SPS_NOM, avg_sps - SPS_NOM);
fprintf('over %d symbols: cumulative drift = %+.1f samples (%+.1f symbol widths)\n', ...
    n_symbols, drift(end), drift(end) / SPS_NOM);

%% ---- plot ----
figure('Name', 'seg017 pick points -- MM lb=0.045 d=0.7 md=1.5', ...
       'Position', [80 80 1500 800]);

ax1 = subplot(3, 1, 1:2);
plot(t, y(lo:hi), 'Color', [0.5 0.5 0.5], 'LineWidth', 0.4); hold on;
plot(idx_fixed,   pick(idx_fixed),   'o', 'Color', [0 0.447 0.741], ...
    'MarkerFaceColor', [0 0.447 0.741], 'MarkerSize', 4);
plot(idx_symsync, pick(idx_symsync), '^', 'Color', [0.85 0.325 0.098], ...
    'MarkerFaceColor', [0.85 0.325 0.098], 'MarkerSize', 4);
grid on; xlabel('sample index'); ylabel('amplitude');
legend({'waveform', ...
        sprintf('01/03 fixed grid (sps=%.3f)', SPS_NOM), ...
        sprintf('Symbol Sync avg grid (sps=%.3f)', avg_sps)}, ...
       'Location', 'northeast');
title(sprintf('seg017 confirmed frame, %d symbols -- zoom in to compare pick points', ...
    n_symbols), 'Interpreter', 'none');

ax2 = subplot(3, 1, 3);
plot(n, drift, 'Color', [0.85 0.325 0.098], 'LineWidth', 1.5); hold on;
yline(0, '--', 'Color', [0.5 0.5 0.5]);
grid on; xlabel('symbol index within frame'); ylabel('sample offset');
title('cumulative divergence between the two grids (Symbol Sync - fixed)');

% The two panels use different x units (samples vs symbol index), so they are
% deliberately NOT linked -- zooming the waveform should not drag the drift
% curve along with it.
zoom on;
