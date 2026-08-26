% tune_decision_points.m -- interactive tool for judging (and improving) WHERE
% the symbol decisions land inside seg017's confirmed frame.
%
% plot_pickpoints_seg017.m answers "where did the two grids sample?".  This
% one answers the follow-up: "could those sampling instants be better?"  It
% draws the eye diagram of the confirmed frame, marks the current decision
% instant on it, and sweeps the sampling phase to show which phase maximises
% the decision margin.  Two knobs at the top (SPS_USE, PHASE_ADJ); change
% them and re-run.
%
% What "margin" means here: binary_slicer_fb decides purely on sign, i.e. it
% compares against THRESH = 0.  So the quantity that matters at a decision
% instant is |y - 0|: the further the sample sits from the threshold, the
% more noise it takes to flip that bit.  The margin curve is the mean of
% that over every symbol in the frame.  It is a proxy for robustness, NOT a
% bit-error rate -- a higher margin means the decisions are less marginal,
% not that any specific bit changed.
%
% Constants below describe the ONE confirmed decode (MM / loop_bw=0.045 /
% damping=0.7 / max_dev=1.5, CRC-passing, header byte-identical to
% REFERENCE_FRAME.md), as printed on 2026-08-26 by:
%   python plot_pickpoints_comparison.py Output/20260723_091639_seg017_510-540s.wav \
%       --ted MM --loop-bw 0.045 --damping 0.7 --max-dev 1.5 --whole
%   -> bit range=[186582, 188783] out of 292483 symbols, avg_sps=4.92336
%
% Note on SPS_USE: 4.92336 is the loop's average over the WHOLE 30 s file,
% and most of that file is noise, where the loop free-runs -- so it is not a
% measurement of this frame's symbol rate.  Default here is therefore the
% nominal 5.0.  Set SPS_USE = 4.92336 and re-run to compare: whichever value
% gives the cleaner, more open eye over the frame is the better description
% of the real symbol rate.

WAV          = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output\20260723_091639_seg017_510-540s.wav';
FRAME_START  = 918610;   % sample index where the confirmed frame begins
N_SYMBOLS    = 2201;     % symbols in that frame

SPS_USE      = 5.0;      % <-- KNOB 1: samples per symbol (try 4.92336 to compare)
PHASE_ADJ    = 0.0;      % <-- KNOB 2: shift every decision instant, in samples

THRESH       = 0.0;      % binary_slicer_fb slices on sign, so the threshold is 0

%% ---- load + build the decision instants ----
[y, fs] = audioread(WAV);
y = y(:, 1);
t_idx = (0:numel(y)-1)';            % 0-based sample positions, to match Python

n     = (0:N_SYMBOLS-1)';
picks = FRAME_START + PHASE_ADJ + SPS_USE * n;

% Fractional positions, so interpolate rather than round -- rounding would
% itself introduce up to half a sample of phase error, which is exactly the
% thing being measured here.
sample_at = @(pos) interp1(t_idx, y, pos, 'linear', NaN);

%% ---- margin as a function of sampling phase ----
ph_grid = linspace(-SPS_USE/2, SPS_USE/2, 201);
margin  = zeros(size(ph_grid));
for i = 1:numel(ph_grid)
    margin(i) = mean(abs(sample_at(picks + ph_grid(i)) - THRESH), 'omitnan');
end
[best_margin, i_best] = max(margin);
best_phase = ph_grid(i_best);
cur_margin = mean(abs(sample_at(picks) - THRESH), 'omitnan');

fprintf('sps = %.5f, current phase adj = %+.3f samples\n', SPS_USE, PHASE_ADJ);
fprintf('  current margin : %.4f\n', cur_margin);
fprintf('  best margin    : %.4f at phase %+.3f samples (%+.3f from current)\n', ...
    best_margin, PHASE_ADJ + best_phase, best_phase);
fprintf('  -> improvement : %+.1f%%\n', 100*(best_margin/cur_margin - 1));

%% ---- eye diagram over the frame ----
n_eye = 201;
tau   = linspace(-SPS_USE, SPS_USE, n_eye);        % +/- one symbol either side
eye   = sample_at(picks + tau);                    % N_SYMBOLS x n_eye, implicit expansion

% Draw all traces as ONE line object separated by NaNs -- 2201 separate
% plot() calls would be painfully slow and would blow up the legend.
Xe = [repmat(tau, N_SYMBOLS, 1), nan(N_SYMBOLS,1)]';
Ye = [eye,                       nan(N_SYMBOLS,1)]';

%% ---- plot ----
figure('Name', 'seg017 decision points -- eye / margin / waveform', ...
       'Position', [60 60 1500 900]);

subplot(2, 2, 1);
plot(Xe(:), Ye(:), 'Color', [0 0.447 0.741 0.06]); hold on;
yline(THRESH, 'k--', 'LineWidth', 1);
xline(0, 'Color', [0.85 0.325 0.098], 'LineWidth', 2);
xline(best_phase, 'Color', [0.466 0.674 0.188], 'LineWidth', 2, 'LineStyle', ':');
grid on; xlabel('offset from decision instant (samples)'); ylabel('amplitude');
title(sprintf('eye diagram (sps=%.4f) -- orange = current, green = best margin', SPS_USE));

subplot(2, 2, 2);
plot(ph_grid, margin, 'Color', [0 0.447 0.741], 'LineWidth', 1.5); hold on;
plot(0, cur_margin, 'o', 'Color', [0.85 0.325 0.098], ...
    'MarkerFaceColor', [0.85 0.325 0.098], 'MarkerSize', 9);
plot(best_phase, best_margin, 'p', 'Color', [0.466 0.674 0.188], ...
    'MarkerFaceColor', [0.466 0.674 0.188], 'MarkerSize', 14);
grid on; xlabel('sampling phase offset (samples)'); ylabel('mean |y - threshold|');
legend({'margin vs phase', 'current', 'best'}, 'Location', 'south');
title('decision margin vs sampling phase');

subplot(2, 2, 3:4);
lo = max(1, floor(picks(1)) - 5);
hi = min(numel(y), ceil(picks(end)) + 5);
tt = (lo:hi)' - 1;                                  % back to 0-based for the x axis
plot(tt, y(lo:hi), 'Color', [0.5 0.5 0.5], 'LineWidth', 0.4); hold on;
plot(picks, sample_at(picks), 'o', 'Color', [0.85 0.325 0.098], ...
    'MarkerFaceColor', [0.85 0.325 0.098], 'MarkerSize', 3);
plot(picks + best_phase, sample_at(picks + best_phase), '^', ...
    'Color', [0.466 0.674 0.188], 'MarkerFaceColor', [0.466 0.674 0.188], ...
    'MarkerSize', 3);
yline(THRESH, 'k--');
grid on; xlabel('sample index'); ylabel('amplitude');
legend({'waveform', sprintf('current picks (phase %+.2f)', PHASE_ADJ), ...
        sprintf('best-margin picks (phase %+.2f)', PHASE_ADJ + best_phase)}, ...
       'Location', 'northeast');
title('decision points on the waveform -- zoom in to inspect');

zoom on;
