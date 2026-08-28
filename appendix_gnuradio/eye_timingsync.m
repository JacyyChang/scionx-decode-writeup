% eye_timingsync.m -- adaptive timing-recovery eye analysis using
% comm.SymbolSynchronizer, with head-to-head comparison against the
% fixed-grid (eye_fixed_grid.m) result.
%
% Parallel to eye_fixed_grid.m: that script slices on a plain uniform
% sps=5.0 grid with NO feedback loop; this one feeds the same frame region
% through MATLAB's comm.SymbolSynchronizer (PLL-based timing recovery) and
% measures how the adaptively-recovered eye compares.
%
% Workflow (validate first, then apply):
%   1. seg017 / seg002 (known-good fixed-grid decodes): confirm the loop
%      tracks at least as well as the fixed grid. If it makes things WORSE
%      (as GNU Radio's symbol_sync did for seg002 -- see README), that is
%      important to know before touching seg010/007.
%   2. seg010 / seg007 (real signal, fixed-grid eye nearly closed): see if
%      adaptive timing recovery opens the eye enough to decode.
%
% ============ TWO TRAPS THIS SCRIPT HAD TO BE FIXED FOR ============
%
% (1) THE INPUT MUST BE AMPLITUDE-NORMALIZED. This is not cosmetic -- it is
%     the difference between a working loop and garbage. DetectorGain (2.7,
%     MATLAB's PAM default) is the assumed slope of the timing-error detector
%     S-curve for UNIT-scale symbols, and the loop-filter gains are derived
%     from it. These .wav files are unnormalized floats (region RMS ~9,
%     peaks ~60), so the effective loop gain came out ~9x too high and the
%     NCO ran away. Measured on seg017, Gardner/BnTs=0.03/zeta=1.0, over a
%     14011-sample region that should yield ~2800 symbols:
%         chunk size:  1     2     5     10    25    50    100   250
%         raw input:   193   163   3381  147   222   73    3079  193
%         normalized:  2758  2758  2756  2756  2756  2756  2756  2756
%     Raw input gives a chaotic symbol count that also depends on how the
%     data is chunked -- i.e. the object was not tracking anything at all.
%     After dividing by the region RMS the output is chunk-invariant and the
%     rate is 0.1969 sym/sample against a nominal 0.2000. Everything below
%     therefore runs on y/NRM, and the reported eye openings are multiplied
%     back by NRM so the numbers stay comparable with eye_fixed_grid.m.
%
% (2) WHERE DOES EACH RECOVERED SYMBOL SIT ON THE WAVEFORM? comm.Symbol-
%     Synchronizer is a RATE-CHANGING block: N input samples in, M output
%     symbols out, with NO per-symbol position information. The obvious
%     mapping "symbol k <-> input sample k*N/M" is only right if the loop
%     rate never changed, and drifts when it did -- plotted that way the
%     pick dots slide off the waveform. Fixed in three stages:
%       a. SWEEP (144 combos, must be fast): process in fixed-size chunks
%          and spread each chunk's symbols evenly over THAT chunk's input
%          span -- piecewise-linear, so it follows rate changes to within a
%          couple of samples. Scoring skips SCORE_MARGIN_SYM symbols at each
%          frame end so the residual error can never pull a noise symbol
%          into the min/max metric.
%       b. BEST COMBO: re-run feeding ONE SAMPLE AT A TIME and record which
%          input sample emitted each symbol. Exact, and only ~0.7 s.
%       c. STROBE LAG: a symbol is emitted a fixed ~1.5 samples after the
%          instant the interpolator actually sampled (interpolator delay +
%          loop tick). A constant lag is fitted by least squares, then
%          refined per symbol inside a one-sample band around it -- the
%          Farrow interpolator's fractional interval mu_k varies symbol to
%          symbol, so no single constant can fit. Measured residual on
%          seg017: 0.32 RMS at constant lag, 0.009 RMS after the band
%          refinement (signal RMS 1.0). The band restriction matters: an
%          unrestricted search wanders in the flat zero-padding runs where
%          many offsets fit the same value. Calibrated lag is +1.48..+1.52
%          for all four TEDs, i.e. structural, as expected.
%
% Chunking also keeps each call under comm.SymbolSynchronizer's hard output
% buffer limit (3116 symbols in R2023b); a single call on the whole region
% silently drops trailing input and warns.
%
% Header/frame positions come from header_correlate_locate.m.

%% ---- configuration ----
OUT_DIR  = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output';
SPS      = 5;           % 48000 / 9600
THRESH   = 0.0;         % binary_slicer_fb slices at sign
LEADIN   = 2500;        % samples before frame start (500 symbols, loop settling)
LEADOUT  = 500;         % samples after frame end
SWEEP_CHUNK      = 250; % chunk size for the sweep's piecewise position map
SCORE_MARGIN_SYM = 40;  % symbols skipped at each frame end when scoring
LAG_GRID  = -8:0.02:8;  % coarse constant-lag search (samples)
LAG_BAND  = 0.5;        % per-symbol refinement band around the constant lag
LAG_STEP  = 0.01;

% Segment definitions.
%   header_start: from header_correlate_locate.m (start of leading flags)
%   frame_start:  content start = header_start + calibrated offset (~161 samples)
%   n_symbols:    from eye_fixed_grid.m presets
% All positions are 0-based absolute sample indices (Python convention).
segments = struct( ...
    'name',         {'seg017', 'seg002', 'seg010', 'seg007'}, ...
    'file',         {'20260723_091639_seg017_510-540s.wav', ...
                      '20260723_091639_seg002_60-90s.wav', ...
                      '20260723_091639_seg010_300-330s.wav', ...
                      '20260720_220206_seg007_210-240s.wav'}, ...
    'header_start', {916252, 1159085, 316927, 1382708}, ...
    'frame_start',  {916413, 1159245, 317088, 1382869}, ...
    'n_symbols',    {2202,   2199,    2200,   2200});

% Parameter sweep grid
TED_NAMES = {'Mueller-Muller (decision-directed)', ...
             'Gardner (non-data-aided)', ...
             'Early-Late (non-data-aided)', ...
             'Zero-Crossing (decision-directed)'};
TED_SHORT = {'MM', 'Gardner', 'EarlyLate', 'ZeroCross'};
LOOP_BWS  = [0.005, 0.01, 0.02, 0.03, 0.045, 0.06];
DAMPINGS  = [0.5, 0.707, 1.0];
DET_GAIN  = 2.7;        % MATLAB default for PAM (assumes unit-scale input!)

HEADER_LEN = 720;       % 144 bits x SPS = 720 samples (4 flags + 14-byte addr)

n_combos = numel(TED_NAMES) * numel(LOOP_BWS) * numel(DAMPINGS) * 2;
fprintf('=== eye_timingsync.m ===\n');
fprintf('Sweep: %d TEDs x %d BnTs x %d zeta x 2 pol = %d combos/segment\n', ...
    numel(TED_NAMES), numel(LOOP_BWS), numel(DAMPINGS), n_combos);

results = cell(1, numel(segments));

%% ---- process each segment ----
for seg_k = 1:numel(segments)
    seg = segments(seg_k);
    fprintf('\n---------- %s ----------\n', seg.name);

    wav_path = fullfile(OUT_DIR, seg.file);
    [y_raw, fs] = audioread(wav_path);
    y_raw = y_raw(:, 1);
    fprintf('  %d samples @ %d Hz (%.2f s)\n', numel(y_raw), fs, numel(y_raw)/fs);

    %% ---- region extraction ----
    % idx_* are 1-based MATLAB indices into the waveform; pos_offset converts
    % a region 1-based index r to an absolute 0-based position: r-1+pos_offset.
    idx_lo = max(1, seg.frame_start - LEADIN + 1);
    idx_hi = min(numel(y_raw), round(seg.frame_start + SPS*seg.n_symbols) + LEADOUT + 1);
    pos_offset = idx_lo - 1;
    n_in = idx_hi - idx_lo + 1;

    % Trap (1): normalize by the region's own RMS. Everything downstream runs
    % on y (normalized); openings are scaled back by NRM before reporting.
    NRM = rms(y_raw(idx_lo:idx_hi));
    y   = y_raw / NRM;
    fprintf('  region %d samples, RMS %.2f -> normalized (peak %.2f)\n', ...
        n_in, NRM, max(abs(y(idx_lo:idx_hi))));

    t_idx = (0:numel(y)-1)';                 % 0-based, matching eye_fixed_grid.m
    sample_at = @(pos) interp1(t_idx, y, pos, 'linear', NaN);

    %% ---- fixed-grid eye opening (for comparison) ----
    n_sym = (0:seg.n_symbols-1)';
    best_fg = struct('opening', -inf, 'phase', NaN, 'pol', NaN);
    for pol = [+1, -1]
        for ph = linspace(0, SPS-0.1, 50)
            v = pol * sample_at(seg.frame_start + ph + SPS * n_sym);
            hi = v(v > THRESH);  lo = v(v <= THRESH);
            if ~isempty(hi) && ~isempty(lo)
                op = min(hi) - max(lo);
                if op > best_fg.opening
                    best_fg.opening = op;
                    best_fg.phase   = ph;
                    best_fg.pol     = pol;
                end
            end
        end
    end
    fprintf('  fixed-grid: opening = %+.2f  (phase=%.2f, pol=%+d)\n', ...
        best_fg.opening * NRM, best_fg.phase, best_fg.pol);

    % frame boundaries as region 1-based sample indices
    frame_lo_r = seg.frame_start - pos_offset + 1;
    frame_hi_r = frame_lo_r + SPS * seg.n_symbols;
    % inner scoring window (margin absorbs residual position-mapping error)
    score_lo_r = frame_lo_r + SPS * SCORE_MARGIN_SYM;
    score_hi_r = frame_hi_r - SPS * SCORE_MARGIN_SYM;

    %% ---- timing sync sweep ----
    % Fixed-size chunks only: comm.SymbolSynchronizer refuses a changed input
    % size without release(), so the tail is zero-padded to a full chunk. The
    % padding's symbols land past the frame end and are filtered out anyway.
    n_pad  = ceil(n_in / SWEEP_CHUNK) * SWEEP_CHUNK;
    best_ts = struct('opening', -inf, 'found', false);
    t_sweep = tic;

    for pol = [+1, -1]
        y_region = pol * y(idx_lo:idx_hi);
        y_padded = [y_region; zeros(n_pad - n_in, 1)];

        for ti = 1:numel(TED_NAMES)
            for bi = 1:numel(LOOP_BWS)
                for di = 1:numel(DAMPINGS)
                    symsync = comm.SymbolSynchronizer( ...
                        'Modulation',              'PAM/PSK/QAM', ...
                        'TimingErrorDetector',      TED_NAMES{ti}, ...
                        'SamplesPerSymbol',         SPS, ...
                        'NormalizedLoopBandwidth',  LOOP_BWS(bi), ...
                        'DampingFactor',            DAMPINGS(di), ...
                        'DetectorGain',             DET_GAIN);

                    sym_v   = zeros(0, 1);
                    sym_pos = zeros(0, 1);
                    for ci = 1:SWEEP_CHUNK:n_pad
                        s = symsync(y_padded(ci:ci+SWEEP_CHUNK-1));
                        k = numel(s);
                        if k > 0
                            sym_v   = [sym_v;   real(s)];                          %#ok<AGROW>
                            % spread this chunk's k symbols over its input span
                            sym_pos = [sym_pos; ci + SWEEP_CHUNK*((0:k-1)'+0.5)/k]; %#ok<AGROW>
                        end
                    end
                    if numel(sym_v) < 100, continue; end

                    fm = sym_pos >= score_lo_r & sym_pos < score_hi_r;
                    fsyms = sym_v(fm);
                    if numel(fsyms) < 100, continue; end

                    hi = fsyms(fsyms >  THRESH);
                    lo = fsyms(fsyms <= THRESH);
                    if isempty(hi) || isempty(lo), continue; end
                    op = min(hi) - max(lo);

                    if op > best_ts.opening
                        best_ts = struct( ...
                            'opening',   op, ...
                            'found',     true, ...
                            'n_scored',  numel(fsyms), ...
                            'pol',       pol, ...
                            'ted',       TED_NAMES{ti}, ...
                            'ted_short', TED_SHORT{ti}, ...
                            'lb',        LOOP_BWS(bi), ...
                            'damp',      DAMPINGS(di));
                    end
                end
            end
        end
    end

    if ~best_ts.found
        fprintf('  timingsync: NO valid result from %d combos\n', n_combos);
        continue;
    end

    fprintf('  sweep (%.1fs): margined opening = %+.2f over %d symbols\n', ...
        toc(t_sweep), best_ts.opening * NRM, best_ts.n_scored);
    fprintf('    winner: %s, BnTs=%.3f, zeta=%.3f, pol=%+d\n', ...
        best_ts.ted_short, best_ts.lb, best_ts.damp, best_ts.pol);

    %% ---- precise re-run: sample-by-sample for exact strobe positions ----
    fprintf('  precise re-run (%d samples, one at a time)...', n_in);
    t_re = tic;
    symsync_p = comm.SymbolSynchronizer( ...
        'Modulation',              'PAM/PSK/QAM', ...
        'TimingErrorDetector',      best_ts.ted, ...
        'SamplesPerSymbol',         SPS, ...
        'NormalizedLoopBandwidth',  best_ts.lb, ...
        'DampingFactor',            best_ts.damp, ...
        'DetectorGain',             DET_GAIN);

    y_re      = best_ts.pol * y(idx_lo:idx_hi);
    exact_pos = zeros(n_in, 1);   % region 1-based input index that emitted the symbol
    exact_val = zeros(n_in, 1);   % the symbol value itself (normalized units)
    exact_te  = zeros(n_in, 1);   % timing error, one per input sample
    n_ex = 0;
    for i = 1:n_in
        [s, te] = symsync_p(y_re(i));
        exact_te(i) = te;
        for j = 1:numel(s)
            n_ex = n_ex + 1;
            exact_pos(n_ex) = i;
            exact_val(n_ex) = real(s(j));
        end
    end
    exact_pos = exact_pos(1:n_ex);
    exact_val = exact_val(1:n_ex);
    fprintf(' %d symbols in %.1fs (%.4f sym/sample, nominal %.4f)\n', ...
        n_ex, toc(t_re), n_ex/n_in, 1/SPS);

    %% ---- strobe-lag calibration (trap 2c) ----
    r_idx = (1:n_in)';
    % step 1: constant lag by least squares over the whole region
    lag_err = nan(size(LAG_GRID));
    for li = 1:numel(LAG_GRID)
        d  = interp1(r_idx, y_re, exact_pos - LAG_GRID(li), 'spline', NaN) - exact_val;
        ok = ~isnan(d);
        if nnz(ok) > 100, lag_err(li) = mean(d(ok).^2); end
    end
    [mse_const, li_best] = min(lag_err);
    lag_const = LAG_GRID(li_best);

    % step 2: refine per symbol inside a one-sample band (the Farrow
    % interpolator's mu_k varies; a single constant cannot fit it)
    d_grid = lag_const + (-LAG_BAND:LAG_STEP:LAG_BAND);
    V      = interp1(r_idx, y_re, exact_pos - d_grid, 'spline', NaN);
    [resid, i_sel] = min(abs(V - exact_val), [], 2);
    strobe_r = exact_pos - d_grid(i_sel)';        % region-relative sampling instant
    ok_r     = ~isnan(resid);
    fprintf('  strobe lag %+.2f samples; fit residual RMS %.4f -> %.4f after per-symbol refine\n', ...
        lag_const, sqrt(mse_const), rms(resid(ok_r)));

    %% ---- frame selection with exact positions ----
    fm_ex = strobe_r >= frame_lo_r & strobe_r < frame_hi_r;
    frame_sym_pos  = strobe_r(fm_ex) - 1 + pos_offset;   % absolute 0-based
    frame_sym_vals = exact_val(fm_ex);
    n_frame = numel(frame_sym_vals);

    hi = frame_sym_vals(frame_sym_vals >  THRESH);
    lo = frame_sym_vals(frame_sym_vals <= THRESH);
    if ~isempty(hi) && ~isempty(lo)
        exact_opening = min(hi) - max(lo);
    else
        exact_opening = -inf;
    end
    delta = (exact_opening - best_fg.opening) * NRM;
    fprintf('  full-frame opening: %+.2f  (%d symbols)   fixed-grid %+.2f   delta %+.2f\n', ...
        exact_opening * NRM, n_frame, best_fg.opening * NRM, delta);

    results{seg_k} = struct('name', seg.name, ...
        'fg_opening', best_fg.opening * NRM, 'fg_phase', best_fg.phase, ...
        'fg_pol', best_fg.pol, ...
        'ts_opening', exact_opening * NRM, 'ts_margined', best_ts.opening * NRM, ...
        'ts_ted', best_ts.ted_short, 'ts_lb', best_ts.lb, 'ts_damp', best_ts.damp, ...
        'ts_pol', best_ts.pol, 'n_frame', n_frame, 'lag', lag_const);

    %% ---- plot (4 panels, parallel to eye_fixed_grid.m) ----
    % Back to RAW amplitude units for display, so the panels are directly
    % comparable with eye_fixed_grid.m's.
    y_plot = best_ts.pol * y_raw;
    plot_val = frame_sym_vals * NRM;   % symbols were computed on pol*y_raw/NRM
    sample_at_plot = @(pos) interp1(t_idx, y_plot, pos, 'linear', NaN);

    figure('Name', sprintf('timingsync eye -- %s', seg.name), ...
           'Position', [60 60 1500 900]);

    % -- Panel 1: eye diagram, folded at the exact recovered instants --
    n_eye   = 251;
    tau     = linspace(-SPS, SPS, n_eye);
    eye_mat = sample_at_plot(frame_sym_pos + tau);    % implicit expansion
    Xe = [repmat(tau, n_frame, 1), nan(n_frame,1)]';
    Ye = [eye_mat,                 nan(n_frame,1)]';

    subplot(2,2,1);
    plot(Xe(:), Ye(:), 'Color', [0 0.447 0.741 0.05]); hold on;
    yline(THRESH, 'k--', 'LineWidth', 1);
    xline(0, 'Color', [0.85 0.325 0.098], 'LineWidth', 2);
    grid on;
    xlabel('offset from recovered decision instant (samples)');
    ylabel('amplitude');
    title(sprintf('eye diagram (timingsync %s, BnTs=%.3f, z=%.3f)', ...
        best_ts.ted_short, best_ts.lb, best_ts.damp));

    % -- Panel 2: timing error trace from the precise re-run --
    subplot(2,2,2);
    plot(r_idx - 1 + pos_offset, exact_te, 'Color', [0 0.447 0.741], 'LineWidth', 0.5);
    hold on;
    xline(seg.frame_start,                     'Color', [0.466 0.674 0.188], 'LineWidth', 2);
    xline(seg.frame_start + SPS*seg.n_symbols, 'Color', [0.466 0.674 0.188], ...
          'LineWidth', 1, 'LineStyle', '--');
    grid on;
    xlabel('sample index');
    ylabel('timing error (normalized)');
    title('timing error -- green = frame start / end');

    % -- Panel 3: decision-sample histogram --
    subplot(2,2,3);
    histogram(lo * NRM, 40, 'FaceColor', [0.85 0.325 0.098]); hold on;
    histogram(hi * NRM, 40, 'FaceColor', [0 0.447 0.741]);
    xline(THRESH, 'k--', 'LineWidth', 1);
    grid on;
    xlabel('symbol amplitude');
    ylabel('count');
    legend({'sliced as 0', 'sliced as 1'}, 'Location', 'north');
    title(sprintf('histogram -- opening %+.2f  (fixed-grid %+.2f)', ...
        exact_opening * NRM, best_fg.opening * NRM));

    % -- Panel 4: waveform + pick points + header span --
    subplot(2,2,4);
    lo_s = max(1, floor(min(frame_sym_pos)) - 5);
    hi_s = min(numel(y_plot), ceil(max(frame_sym_pos)) + 5);
    lo_s = max(1, min(lo_s, seg.header_start - 20));
    tt = (lo_s:hi_s)' - 1;               % 0-based x axis
    plot(tt, y_plot(lo_s:hi_s), 'Color', [0.5 0.5 0.5], 'LineWidth', 0.4); hold on;

    yl = [min(y_plot(lo_s:hi_s)), max(y_plot(lo_s:hi_s))];
    hs = seg.header_start;
    patch([hs, hs+HEADER_LEN, hs+HEADER_LEN, hs], ...
          [yl(1) yl(1) yl(2) yl(2)], [0.9290 0.6940 0.1250], ...
          'FaceAlpha', 0.20, 'EdgeColor', 'none');
    xline(hs,              'Color', [0.6940 0.4940 0.1250], 'LineWidth', 1.5);
    xline(hs+HEADER_LEN-1, 'Color', [0.6940 0.4940 0.1250], 'LineWidth', 1.5, ...
          'LineStyle', '--');

    plot(frame_sym_pos, plot_val, 'o', 'Color', [0 0.447 0.741], ...
         'MarkerFaceColor', [0 0.447 0.741], 'MarkerSize', 3);
    yline(THRESH, 'k--');
    grid on;
    xlabel('sample index');
    ylabel('amplitude');
    title(sprintf('timingsync picks (strobe lag %+.2f removed) + header span -- zoom in', ...
        lag_const));
    zoom on;

    % super-title
    [~, wav_stem] = fileparts(seg.file);
    sgtitle({wav_stem, ...
        sprintf('comm.SymbolSynchronizer: %s, BnTs=%.3f, zeta=%.3f, pol=%+d, %d symbols', ...
            best_ts.ted_short, best_ts.lb, best_ts.damp, best_ts.pol, n_frame), ...
        sprintf('eye opening: timingsync %+.2f  vs  fixed-grid %+.2f  (delta %+.2f)', ...
            exact_opening * NRM, best_fg.opening * NRM, delta)}, ...
        'Interpreter', 'none', 'FontSize', 11, 'FontWeight', 'bold');

    % save
    fig_path = fullfile(OUT_DIR, sprintf('eye_timingsync_%s.fig', wav_stem));
    png_path = fullfile(OUT_DIR, sprintf('eye_timingsync_%s.png', wav_stem));
    savefig(gcf, fig_path);
    print(gcf, png_path, '-dpng', '-r150');
    fprintf('  saved -> %s\n', fig_path);
end

%% ---- summary table ----
fprintf('\n============================= SUMMARY =============================\n');
fprintf('%-8s  %11s  %11s  %7s  %-10s  %6s  %6s  %4s  %6s\n', ...
    'Segment', 'Fixed-grid', 'TimingSync', 'Delta', 'TED', 'BnTs', 'Zeta', 'Pol', 'Lag');
fprintf('%s\n', repmat('-', 1, 86));
for i = 1:numel(results)
    if isempty(results{i}), continue; end
    r = results{i};
    fprintf('%-8s  %+11.2f  %+11.2f  %+7.2f  %-10s  %6.3f  %6.3f  %+4d  %+6.2f\n', ...
        r.name, r.fg_opening, r.ts_opening, r.ts_opening - r.fg_opening, ...
        r.ts_ted, r.ts_lb, r.ts_damp, r.ts_pol, r.lag);
end
fprintf('\nPositive delta = timingsync improved over fixed-grid.\n');
fprintf('Negative delta = timingsync made things WORSE (loop dragged timing off).\n');
fprintf('Lag = calibrated strobe delay removed before plotting; it is structural\n');
fprintf('      (~+1.5 samples) and should be near-identical for every segment.\n');
