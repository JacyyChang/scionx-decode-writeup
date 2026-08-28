% -*- coding: utf-8 -*-
% clock_tone_locate.m -- feedforward, one-shot symbol-TIMING ESTIMATION for
% the SCION-X / RANGE-A GFSK 9600 downlink, as an alternative to both the
% plain fixed grid (eye_fixed_grid.m) and the closed-loop PLL
% (eye_timingsync.m). NO feedback loop, NO Communications Toolbox.
%
% WHY THIS EXISTS (the whole argument in three lines):
%   * A pure PLL (comm.SymbolSynchronizer) was WORSE than a fixed sps=5 grid
%     on all four test segments -- its non-data-aided TEDs get an identically
%     zero error signal through the long all-zero HDLC padding runs (AX.25
%     here carries NO NRZI/scrambling, so a zero run is a literal DC tone
%     with no transitions), and the loop free-runs to a biased rate and
%     holds it. seg002's timing-error trace is a clean sawtooth ramp; seg007
%     emitted only 0.164 sym/sample (should be 0.200) -- a lost lock.
%   * A rectangular NRZ signal has a spectral NULL at the symbol rate 1/T
%     (its PSD is sinc^2), so there is no line to lock onto in the first
%     place. The nonlinearity that regenerates a strong line at 1/T for NRZ
%     is the SQUARED DERIVATIVE (dx/dt)^2: it pulses at every transition and
%     is ~0 in flat runs. |x|^2 (the usual Oerder-Meyr choice) is useless
%     here because x^2 is nearly constant for a +/-A NRZ waveform.
%   * The fixed grid decodes 2200 symbols of seg017/seg002 with a passing
%     CRC at a CONSTANT sps=5.0, which means the intra-frame clock drift is
%     only tenths of a sample (Doppler ~25 ppm + SDR TCXO a few ppm). There
%     is essentially no drift to track -- what could help is a better ONE-
%     TIME estimate of the symbol period T and the sampling phase phi, not
%     continuous adaptation.
%
% METHOD (Oerder-Meyr style, squared-derivative variant):
%   g[m] = (x[m+1]-x[m])^2 , demeaned.  Then scan the symbol period T and
%   evaluate the clock-tone DFT bin
%       S(T) = sum_m g[m] * exp(-1j*2*pi*pos[m]/T)
%   T_hat = argmax_T |S(T)| ; the phase of S(T_hat) gives the sampling phase.
%   Apply idx(n) = phi + T_hat*n OPEN-LOOP. Flat runs contribute g~0 so they
%   are weighted out automatically (no gating needed); the estimator is
%   exactly amplitude-scale-invariant (immune to the DetectorGain bug that
%   plagued the PLL); every pick position is a closed-form number (immune to
%   the rate-changing-block position-mapping bug).
%
% The |S(T)| curve is also the deliverable diagnostic: with a z-score
% against its own background it says whether a recoverable clock EXISTS in a
% segment at all. If seg010/seg007 show a strong line AT the nominal rate,
% timing is exonerated and the blocker is elsewhere (baseline/SNR).
%
% Reuses verbatim: the segment table from eye_timingsync.m, and the
% interp1-based sample_at + min/max eye-opening metric from eye_fixed_grid.m.
% Outputs (figures, JSON) go into this Recovery_try folder.

%% ---- configuration ----
cfg.OUT_DIR  = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output';
cfg.FIG_DIR  = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Recovery_try';
cfg.SPS_NOM  = 5;                       % 48000/9600
cfg.T_GRID   = 4.90 : 0.0002 : 5.10;    % symbol-period search grid (samples), 1001 pts
cfg.N_BLOCKS = 24;                      % per-block phase diagnostic only
cfg.BG_HALFWIDTH = 0.02;                % |T-T_hat|>this defines the z-score background

% Segment table (0-based ABSOLUTE sample indices), copied from eye_timingsync.m
segments = struct( ...
    'name',         {'seg017', 'seg002', 'seg010', 'seg007'}, ...
    'file',         {'20260723_091639_seg017_510-540s.wav', ...
                      '20260723_091639_seg002_60-90s.wav', ...
                      '20260723_091639_seg010_300-330s.wav', ...
                      '20260720_220206_seg007_210-240s.wav'}, ...
    'header_start', {916252, 1159085, 316927, 1382708}, ...
    'frame_start',  {916413, 1159245, 317088, 1382869}, ...
    'n_symbols',    {2202,   2199,    2200,   2200});

CONV_NAMES = {'-T*th/2pi + T/2', '-T*th/2pi', '+T*th/2pi + T/2', '+T*th/2pi'};

fprintf('=== clock_tone_locate.m (squared-derivative Oerder-Meyr) ===\n');
fprintf('T grid: %.4f .. %.4f step %.4f (%d pts)\n', ...
    cfg.T_GRID(1), cfg.T_GRID(end), cfg.T_GRID(2)-cfg.T_GRID(1), numel(cfg.T_GRID));

%% ---- analyze every segment once ----
Rall = cell(1, numel(segments));
for k = 1:numel(segments)
    Rall{k} = analyze_segment(segments(k), cfg);
end

%% ---- determine the sign/half-symbol convention empirically on seg017 ----
% The four phase candidates differ by an overall sign and a half-symbol
% shift; the correct one is whichever maximizes the eye opening on a segment
% we KNOW decodes (seg017). Then require the same index to win on seg002.
[~, best_pol1] = max(Rall{1}.opening_mat, [], 2);   % best pol per conv
best_open_per_conv = max(Rall{1}.opening_mat, [], 2);
[~, CONV_IDX] = max(best_open_per_conv);
fprintf('\nConvention disambiguation on seg017:\n');
for c = 1:4
    fprintf('  conv %d (%-16s): best opening %+6.2f (pol %+d)\n', ...
        c, CONV_NAMES{c}, best_open_per_conv(c), 3-2*best_pol1(c));
end
fprintf('  -> CONV_IDX = %d\n', CONV_IDX);

[~, cross_pol] = max(Rall{2}.opening_mat, [], 2);
cross_open = max(Rall{2}.opening_mat, [], 2);
[~, cross_idx] = max(cross_open);
fprintf('Cross-check on seg002: preferred conv = %d (opening %+.2f). %s\n', ...
    cross_idx, cross_open(cross_idx), ...
    ternary(cross_idx==CONV_IDX, 'MATCHES seg017.', ...
            'DOES NOT MATCH -- estimator convention is unstable, investigate.'));

%% ---- plot + JSON + summary for every segment using the chosen convention ----
summary = cell(numel(segments), 1);
for k = 1:numel(segments)
    R = Rall{k};
    seg = segments(k);

    % chosen phase + polarity for this segment under the fixed convention
    phi_smp = R.cands(CONV_IDX);
    [opening, pol_idx] = max(R.opening_mat(CONV_IDX, :));
    pol = 3 - 2*pol_idx;                 % 1->+1, 2->-1
    nhi = R.nhi_mat(CONV_IDX, pol_idx);
    nlo = R.nlo_mat(CONV_IDX, pol_idx);
    rob = R.rob_mat(CONV_IDX, pol_idx);

    drift = (R.T_hat - cfg.SPS_NOM) * seg.n_symbols;   % samples slipped across frame

    fprintf('\n---------- %s ----------\n', seg.name);
    fprintf('  T_hat = %.5f samples/symbol  (%.1f ppm, drift %.2f samples over %d symbols)\n', ...
        R.T_hat, R.ppm, drift, seg.n_symbols);
    fprintf('  clock-tone z = %.2f   phase(sampling) = %.3f   polarity = %+d\n', ...
        R.z, phi_smp, pol);
    fprintf('  eye opening: hybrid %+.2f (%d hi / %d lo, robust %+.2f)  vs  fixed-grid %+.2f\n', ...
        opening, nhi, nlo, rob, R.fixed.opening);
    fprintf('  split-half: T_first = %.5f, T_second = %.5f, |diff| = %.5f  %s\n', ...
        R.T_first, R.T_second, abs(R.T_first-R.T_second), ...
        ternary(abs(R.T_first-R.T_second) > 0.01, ...
                '(> 0.01: single-line model may under-fit)', '(consistent)'));

    % write JSON handoff
    [~, stem] = fileparts(seg.file);
    write_json(fullfile(cfg.FIG_DIR, sprintf('clock_tone_%s.json', stem)), ...
        seg, R, phi_smp, pol, opening);

    % 6-panel figure
    plot_segment(seg, R, cfg, CONV_IDX, phi_smp, pol, opening, drift);

    summary{k} = struct('name', seg.name, 'T_hat', R.T_hat, 'ppm', R.ppm, ...
        'z', R.z, 'drift', drift, 'fixed', R.fixed.opening, ...
        'hybrid', opening, 'pol', pol);
end

%% ---- summary table ----
fprintf('\n===================================== SUMMARY =====================================\n');
fprintf('%-8s %9s %8s %7s %10s %10s %10s %7s\n', ...
    'Segment', 'T_hat', 'ppm', 'z', 'drift/fr', 'fixed', 'hybrid', 'delta');
fprintf('%s\n', repmat('-', 1, 82));
for k = 1:numel(summary)
    s = summary{k};
    fprintf('%-8s %9.5f %8.1f %7.2f %10.2f %10.2f %10.2f %+7.2f\n', ...
        s.name, s.T_hat, s.ppm, s.z, s.drift, s.fixed, s.hybrid, s.hybrid - s.fixed);
end
fprintf('\ndrift/fr = (T_hat-5)*n_symbols, samples slipped across the frame.\n');
fprintf('  >~1 sample: the fixed sps=5 grid was doomed, a corrected T should help.\n');
fprintf('  <<1 sample: a clock line exists at ~nominal rate; timing is NOT the blocker\n');
fprintf('             (investigate baseline restoration / SNR instead).\n');


%% ================= local functions =================

function R = analyze_segment(seg, cfg)
% Full feedforward analysis of one segment. Returns everything needed for
% reporting and plotting; does NOT depend on the chosen convention (it
% computes all four candidate phases and both polarities up front).
    [y, fs] = audioread(fullfile(cfg.OUT_DIR, seg.file));
    y = y(:, 1);
    if fs ~= 48000
        warning('%s: fs=%d (expected 48000)', seg.file, fs);
    end

    % analysis region: header_start (transition-rich preamble) .. frame end
    ana_lo0 = seg.header_start;                              % 0-based inclusive
    ana_hi0 = seg.frame_start + cfg.SPS_NOM * seg.n_symbols; % 0-based
    ilo = ana_lo0 + 1;                                       % 1-based
    ihi = min(numel(y), ana_hi0 + 1);
    x = y(ilo:ihi);

    % squared-derivative nonlinearity (regenerates the 1/T clock line for NRZ)
    d = diff(x);
    g = d.^2;
    g = g - mean(g);                    % suppress DC leakage into the scan
    % d(i) is centred between samples i and i+1 -> 0-based absolute position:
    pos = ana_lo0 + (1:numel(g))' - 0.5;

    % scan the symbol period
    S = zeros(size(cfg.T_GRID));
    for kk = 1:numel(cfg.T_GRID)
        S(kk) = sum(g .* exp(-1j*2*pi*pos / cfg.T_GRID(kk)));
    end

    % peak + parabolic vertex refinement on |S|
    [~, kp] = max(abs(S));
    if kp > 1 && kp < numel(cfg.T_GRID)
        y1 = abs(S(kp-1)); y2 = abs(S(kp)); y3 = abs(S(kp+1));
        denom = (y1 - 2*y2 + y3);
        delta = 0.5*(y1 - y3) / denom;
        if ~isfinite(delta) || abs(delta) > 1, delta = 0; end
    else
        delta = 0;
    end
    hstep = cfg.T_GRID(2) - cfg.T_GRID(1);
    T_hat = cfg.T_GRID(kp) + delta*hstep;

    % recompute the complex value exactly at T_hat (never interpolate it)
    S_hat = sum(g .* exp(-1j*2*pi*pos / T_hat));

    % z-score against the off-peak background
    bg = abs(S(abs(cfg.T_GRID - T_hat) > cfg.BG_HALFWIDTH));
    z  = (abs(S_hat) - mean(bg)) / std(bg);
    ppm = (T_hat - cfg.SPS_NOM) / cfg.SPS_NOM * 1e6;

    % four candidate phase conventions (absolute 0-based phase mod T_hat)
    th = angle(S_hat);
    cands = [ mod(-T_hat*th/(2*pi) + T_hat/2, T_hat), ...
              mod(-T_hat*th/(2*pi),            T_hat), ...
              mod( T_hat*th/(2*pi) + T_hat/2,  T_hat), ...
              mod( T_hat*th/(2*pi),            T_hat) ];

    % eye opening for each (conv, polarity), on the raw waveform
    t_idx = (0:numel(y)-1)';
    opening_mat = -inf(4, 2);
    nhi_mat = zeros(4, 2); nlo_mat = zeros(4, 2); rob_mat = nan(4, 2);
    for c = 1:4
        picks = build_picks(cands(c), T_hat, seg.frame_start, seg.n_symbols);
        for pj = 1:2
            pol = 3 - 2*pj;
            v  = pol * interp1(t_idx, y, picks, 'linear', NaN);
            v  = v(~isnan(v));
            hi = v(v > 0); lo = v(v <= 0);
            nhi_mat(c, pj) = numel(hi); nlo_mat(c, pj) = numel(lo);
            if ~isempty(hi) && ~isempty(lo)
                opening_mat(c, pj) = min(hi) - max(lo);
                rob_mat(c, pj) = prctile(hi, 1) - prctile(lo, 99);
            end
        end
    end

    % fixed-grid reference (reproduce eye_timingsync.m's comparison exactly)
    n = (0:seg.n_symbols-1)';
    fixed = struct('opening', -inf, 'phase', NaN, 'pol', NaN);
    for pol = [+1, -1]
        for ph = linspace(0, cfg.SPS_NOM-0.1, 50)
            v = pol * interp1(t_idx, y, seg.frame_start + ph + cfg.SPS_NOM*n, 'linear', NaN);
            hi = v(v > 0); lo = v(v <= 0);
            if ~isempty(hi) && ~isempty(lo)
                op = min(hi) - max(lo);
                if op > fixed.opening
                    fixed.opening = op; fixed.phase = ph; fixed.pol = pol;
                end
            end
        end
    end

    % per-block phase decomposition (diagnostic) -- evaluated at NOMINAL rate
    % so drift shows up as rotation of the block phasor
    edges = round(linspace(1, numel(g)+1, cfg.N_BLOCKS+1));
    p_b = nan(cfg.N_BLOCKS,1); phi_b = nan(cfg.N_BLOCKS,1); w_b = nan(cfg.N_BLOCKS,1);
    for b = 1:cfg.N_BLOCKS
        I = edges(b):edges(b+1)-1;
        Xb = sum(g(I) .* exp(-1j*2*pi*pos(I)/cfg.SPS_NOM));
        phi_b(b) = mod(-cfg.SPS_NOM*angle(Xb)/(2*pi), cfg.SPS_NOM);
        w_b(b)   = abs(Xb);
        p_b(b)   = mean(pos(I));
    end

    % split-half consistency of T_hat
    half = floor(numel(g)/2);
    T_first  = scan_period(g(1:half),      pos(1:half),      cfg);
    T_second = scan_period(g(half+1:end),  pos(half+1:end),  cfg);

    R = struct('T_hat', T_hat, 'S', S, 'S_hat', S_hat, 'z', z, 'ppm', ppm, ...
        'cands', cands, 'opening_mat', opening_mat, 'nhi_mat', nhi_mat, ...
        'nlo_mat', nlo_mat, 'rob_mat', rob_mat, 'fixed', fixed, ...
        'p_b', p_b, 'phi_b', phi_b, 'w_b', w_b, ...
        'T_first', T_first, 'T_second', T_second, 'ana_lo0', ana_lo0);
end

function T_hat = scan_period(g, pos, cfg)
% Minimal argmax|S(T)| for the split-half check (no refinement).
    S = zeros(size(cfg.T_GRID));
    for kk = 1:numel(cfg.T_GRID)
        S(kk) = sum(g .* exp(-1j*2*pi*pos / cfg.T_GRID(kk)));
    end
    [~, kp] = max(abs(S));
    T_hat = cfg.T_GRID(kp);
end

function picks = build_picks(phi_smp, T_hat, frame_start, n_symbols)
% Open-loop grid idx(n) = phi + T_hat*n, anchored to the first pick at or
% after frame_start.
    n0 = ceil((frame_start - phi_smp) / T_hat);
    picks = phi_smp + T_hat * (n0 + (0:n_symbols-1)');
end

function plot_segment(seg, R, cfg, CONV_IDX, phi_smp, pol, opening, drift)
    [y, ~] = audioread(fullfile(cfg.OUT_DIR, seg.file));
    y = y(:, 1);
    t_idx = (0:numel(y)-1)';
    sample_at = @(p) interp1(t_idx, y, p, 'linear', NaN);
    picks = build_picks(phi_smp, R.T_hat, seg.frame_start, seg.n_symbols);
    yp = pol * y;
    sample_at_p = @(p) interp1(t_idx, yp, p, 'linear', NaN);
    [~, wav_stem] = fileparts(seg.file);

    blue = [0 0.447 0.741]; orange = [0.85 0.325 0.098];
    green = [0.466 0.674 0.188]; yellow = [0.9290 0.6940 0.1250];

    figure('Name', sprintf('clock-tone -- %s', seg.name), 'Position', [50 40 1500 1000]);

    % Panel 1: |S(T)| clock-tone spectrum
    subplot(2,3,1);
    plot(cfg.T_GRID, abs(R.S), 'Color', blue, 'LineWidth', 1.2); hold on;
    xline(R.T_hat, 'Color', orange, 'LineWidth', 2);
    xline(cfg.SPS_NOM, 'Color', green, 'LineWidth', 1.5, 'LineStyle', '--');
    grid on; xlabel('symbol period T (samples)'); ylabel('|S(T)|');
    title(sprintf('clock tone: T=%.4f, z=%.1f, %.0f ppm', R.T_hat, R.z, R.ppm));

    % Panel 2: per-block phase vs position (is drift linear?)
    subplot(2,3,2);
    slope = (R.T_hat - cfg.SPS_NOM) / cfg.SPS_NOM;        % samples of phase per sample
    ppred = mod(pho_line(R.p_b, pho_ref(R.p_b, R.phi_b, R.w_b), slope), cfg.SPS_NOM);
    sz = 20 + 180 * (R.w_b / max(R.w_b + eps));
    scatter(R.p_b, R.phi_b, sz, blue, 'filled'); hold on;
    plot(R.p_b, ppred, '-', 'Color', orange, 'LineWidth', 1.2);
    yline(mean(R.phi_b, 'omitnan'), 'Color', green, 'LineStyle', '--');
    grid on; xlabel('sample index (0-based)'); ylabel('block boundary phase (mod 5)');
    title('per-block phase (size \propto info); orange = T\_hat slope');

    % Panel 3: per-block clock-tone energy (where does timing info live?)
    subplot(2,3,3);
    stem(R.p_b, R.w_b, 'Color', blue, 'MarkerFaceColor', blue); hold on;
    yl = ylim;
    patch([seg.header_start, seg.header_start+720, seg.header_start+720, seg.header_start], ...
          [0 0 yl(2) yl(2)], yellow, 'FaceAlpha', 0.25, 'EdgeColor', 'none');
    grid on; xlabel('sample index (0-based)'); ylabel('|block phasor| (timing info)');
    title('timing information per block (yellow = 720-sample preamble)');

    % Panel 4: eye diagram on the CORRECTED grid
    subplot(2,3,4);
    tau = linspace(-R.T_hat, R.T_hat, 251);
    eye = sample_at_p(picks + tau);
    Xe = [repmat(tau, numel(picks), 1), nan(numel(picks),1)]';
    Ye = [eye,                          nan(numel(picks),1)]';
    plot(Xe(:), Ye(:), 'Color', [blue 0.05]); hold on;
    yline(0, 'k--'); xline(0, 'Color', orange, 'LineWidth', 2);
    grid on; xlabel('offset from decision instant (samples)'); ylabel('amplitude');
    yl4 = ylim;
    title(sprintf('CORRECTED grid (T=%.4f) -- opening %+.2f', R.T_hat, opening));

    % Panel 5: eye diagram on the plain sps=5.0 grid at its best phase
    subplot(2,3,5);
    n = (0:seg.n_symbols-1)';
    picks5 = seg.frame_start + R.fixed.phase + cfg.SPS_NOM*n;
    yp5 = R.fixed.pol * y;
    eye5 = interp1(t_idx, yp5, picks5 + tau, 'linear', NaN);
    Xe5 = [repmat(tau, numel(picks5), 1), nan(numel(picks5),1)]';
    Ye5 = [eye5,                          nan(numel(picks5),1)]';
    plot(Xe5(:), Ye5(:), 'Color', [blue 0.05]); hold on;
    yline(0, 'k--'); xline(0, 'Color', green, 'LineWidth', 2);
    grid on; xlabel('offset from decision instant (samples)'); ylabel('amplitude');
    ylim(yl4);                              % SAME y-limits as panel 4 (honest compare)
    title(sprintf('plain sps=5.0 grid -- opening %+.2f', R.fixed.opening));

    % Panel 6: waveform + corrected picks + header span
    subplot(2,3,6);
    lo_s = max(1, min(floor(min(picks))-5, seg.header_start-20));
    hi_s = min(numel(yp), ceil(max(picks))+5);
    tt = (lo_s:hi_s)' - 1;
    plot(tt, yp(lo_s:hi_s), 'Color', [0.5 0.5 0.5], 'LineWidth', 0.4); hold on;
    yl = [min(yp(lo_s:hi_s)), max(yp(lo_s:hi_s))];
    hs = seg.header_start;
    patch([hs hs+720 hs+720 hs], [yl(1) yl(1) yl(2) yl(2)], yellow, ...
          'FaceAlpha', 0.20, 'EdgeColor', 'none');
    xline(hs, 'Color', [0.694 0.494 0.125], 'LineWidth', 1.5);
    xline(hs+719, 'Color', [0.694 0.494 0.125], 'LineWidth', 1.5, 'LineStyle', '--');
    plot(picks, sample_at_p(picks), 'o', 'Color', blue, ...
         'MarkerFaceColor', blue, 'MarkerSize', 3);
    yline(0, 'k--'); grid on; xlabel('sample index'); ylabel('amplitude');
    title('corrected picks on waveform (zoom in)'); zoom on;

    sgtitle({wav_stem, ...
        sprintf('clock-tone T=%.5f (%.0f ppm, z=%.1f, drift %.2f samp/frame), pol=%+d', ...
            R.T_hat, R.ppm, R.z, drift, pol), ...
        sprintf('eye opening: hybrid %+.2f  vs  fixed-grid %+.2f  (delta %+.2f)', ...
            opening, R.fixed.opening, opening - R.fixed.opening)}, ...
        'Interpreter', 'none', 'FontSize', 11, 'FontWeight', 'bold');

    savefig(gcf, fullfile(cfg.FIG_DIR, sprintf('clock_tone_%s.fig', wav_stem)));
    print(gcf, fullfile(cfg.FIG_DIR, sprintf('clock_tone_%s.png', wav_stem)), '-dpng', '-r150');
    fprintf('  saved -> clock_tone_%s.png\n', wav_stem);
end

function write_json(path, seg, R, phi_smp, pol, opening)
    [~, base, ext] = fileparts(seg.file);
    fid = fopen(path, 'w');
    fprintf(fid, ['{"file":"%s","header_start":%d,"frame_start":%d,', ...
        '"n_symbols":%d,"sps_est":%.6f,"phase_sample":%.4f,"polarity":%d,', ...
        '"z":%.3f,"ppm":%.3f,"opening_hybrid":%.4f,"opening_fixed":%.4f}\n'], ...
        [base ext], seg.header_start, seg.frame_start, seg.n_symbols, ...
        R.T_hat, phi_smp, pol, R.z, R.ppm, opening, R.fixed.opening);
    fclose(fid);
end

function out = ternary(cond, a, b)
    if cond, out = a; else, out = b; end
end

% --- tiny helpers for the panel-2 drift line (kept trivial on purpose) ---
function ref = pho_ref(p_b, phi_b, w_b)
% weighted-mean phase at the weighted-mean position (an anchor for the line)
    ok = isfinite(phi_b) & isfinite(w_b);
    ref = sum(phi_b(ok).*w_b(ok)) / sum(w_b(ok));
end

function line = pho_line(p_b, ref, slope)
% straight line of the predicted (unwrapped) phase drift through the anchor
    pc = mean(p_b, 'omitnan');
    line = ref + slope * (p_b - pc);
end
