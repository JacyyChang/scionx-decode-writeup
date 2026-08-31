% s05b_header_locate_decode.m -- Step 2 of the 05_GNURadio_Czechia pipeline
% (code name 05b): given ANY wav file (typically 05a's whole-recording
% output, un-cropped), locate every candidate header position via
% cross-correlation, GROUP candidates into one per (30s-block "seg", pol),
% then attempt an actual CRC-16/X.25 decode for each seg -- replacing BOTH
% power_dip_locate.m (coarse power-dip localization) AND
% appendix_gnuradio/header_correlate_locate.m's hardcoded-4-segment,
% coarse-candidate-dependent fine search, in one script that takes any
% recording and tells you which seg(s) actually decode.
%
% ORDER: this now runs BEFORE 05c_destuff_interactive.py (formerly the other
% way around, and formerly two separate files -- 05c_crop_wav_segment.py +
% 05d_destuff_interactive.py -- merged 2026-08-31 since crop-then-inspect
% is almost always used back to back) -- run this first against 05a's whole
% output to see which seg(s) are worth a closer look (CRC pass, or a
% strong-but-failing candidate worth 05c's manual destuff tool), THEN
% 05c_destuff_interactive.py --seg N (crops + destuffs in one call; add
% --crop-only for just the wav, no Excel). No more guessing a seg number
% off a spectrogram PNG before knowing whether it even contains a real frame.
%
% NAMING NOTE: MATLAB script filenames must be valid identifiers (run() and
% typing the bare name both fail otherwise -- confirmed empirically: a file
% named e.g. "05z_test.m" cannot be run at all, MATLAB parses "05z_test" as
% an expression and errors). That is why every .m file in this whole repo,
% not just this one, has never carried a numeric prefix -- only .py scripts
% do (Python has no such restriction). Hence "s05b_" here (letter-first,
% still reads as "step 05b") instead of "05b_".
%
% WHY THIS REPLACES power_dip_locate.m (confirmed, not assumed): that script
% is a COARSE localizer whose whole reason to exist was to hand
% header_correlate_locate.m a starting point so it only had to
% cross-correlate a +/-10000-sample window instead of a whole file. Measured
% 2026-08-31: MATLAB's conv() auto-switches to FFT-based convolution at these
% sizes, so a FULL-FILE cross-correlation search takes 0.34s for a 30s clip
% and 0.77s for a 300s (5 min) recording -- i.e. searching the whole file
% directly was always affordable, the coarse pre-filter step was never
% actually load-bearing. power_dip_locate.m's own docstring even says to
% fall back to header cross-correlation when the two disagree ("the dip
% method should not be trusted blindly"), i.e. it already considered header
% correlation the more trustworthy method.
%
% METHOD (unchanged core from header_correlate_locate.m, generalized):
% build an NRZ template from the 4 leading HDLC flags (0x7E x4, LSB-first)
% + the 14-byte Dest+Src address (fixed protocol content for this satellite,
% same for every frame -- see REFERENCE_FRAME.md), upsample by SPS, and
% slide it across the WHOLE waveform computing a Pearson-style (template
% zero-meaned) normalized cross-correlation z-score. Every LOCAL max above
% Z_THRESH, at least one template-length apart (non-max suppression), is
% kept as a raw candidate -- a long recording can hold more than one real
% frame (see REFERENCE_FRAME.md: cut_first3.ogg alone holds 2-3).
%
% SEG GROUPING (new): raw candidates are further grouped by
% (seg = floor(pos / (SEG_LEN_SEC*fs)), polarity) -- one real frame's own
% correlation sidelobes commonly produce several raw candidates within the
% SAME 30s block (e.g. seg017 alone produced 4 in initial testing), and
% decoding each separately would both waste time and print the same frame's
% result multiple times under one seg label. Only the highest-z raw
% candidate in each group is used as the decode anchor.
%
% DECODE (new, windowed): one Python call PER (seg, pol) group -- not per
% polarity across the whole file like the previous version. Each call
% restricts 05b_decode_at_grid.py's search to a ~22000-sample window around
% that group's anchor (--start-sample/--end-sample), which cuts
% offline_deframe's pure-Python per-bit scan from ~37M samples to ~22000:
% measured 2026-08-31, a whole-file scan took ~107-122s per polarity call,
% the same decode restricted to one seg's window took 15.3s (and reproduced
% seg017's CRC-passing decode bit-identically) -- fast enough to afford one
% call per candidate group instead of batching by polarity, which is what
% makes "which seg does this decoded frame belong to" answerable at all
% (offline_deframe itself never reports bit/sample position, only frame
% bytes -- windowing the search is what pins a result to a specific seg).
% --seg-label is passed through so Python's own CRC_PASS/NO_CRC_PASS lines
% are self-labeled too, not just this script's summary table.
%
% Deliberately NOT attempted here (would need a much larger validated
% change): MATLAB re-implementing HDLC destuffing + CRC-16/X.25 itself.
% appendix_gnuradio/symbol_sync_sweep.py's offline_deframe/fcs_ok is already
% proven bit-exact against gr-satellites' real hdlc_deframer
% (hdlc_bitorder_test.grc); calling out to it keeps ONE implementation
% instead of two that could silently drift apart.

%% ---- configuration -- edit these for a new recording ----
WAV        = 'D:\Research\3_SCIONX GNURadio\1_Share\05_GNURadio_Czechia\Output\20260723_091639_48k.wav';
START_SEC  = 0;              % restrict the search to [START_SEC, END_SEC); 0/[] = whole file
END_SEC    = [];              % [] = to end of file
SPS        = 5;               % 48000/9600 nominal; only the header template's upsampling uses this
FS_EXPECT  = 48000;
Z_THRESH   = 6.0;              % candidate must clear this z-score (04a's z-threshold idiom)
HEADER_LEN_SAMPLES = 720;      % 144 bits (4 flags + 14-byte addr) * SPS -- template width
SEG_LEN_SEC = 30;              % matches 05a's start_periodic_plot(30) / Figure/*_seg###_spec_*.png

PYTHON_EXE = 'python';         % override to a full path (e.g. radioconda's python.exe) if
                                % `python` on PATH doesn't have numpy+soundfile
DECODE_SCRIPT = fullfile(fileparts(mfilename('fullpath')), '05b_decode_at_grid.py');
SCAN_PHASE = 2.5;              % +/- samples around each group's anchor phase (half a symbol period)
SCAN_PHASE_STEP = 0.1;
WINDOW_LEAD  = 2000;            % decode window = [anchor-WINDOW_LEAD, anchor+WINDOW_TRAIL]
WINDOW_TRAIL = 20000;           % covers header + full 274-byte payload+FCS (~11000 samples) with margin

% -- known-protocol template: 4x flag (0x7E) + 14-byte Dest+Src address --
FLAGS4      = uint8([0x7E 0x7E 0x7E 0x7E]);
HEADER_ADDR = uint8([hex2dec('84') hex2dec('9c') hex2dec('60') hex2dec('86') ...
                     hex2dec('aa') hex2dec('40') hex2dec('60') hex2dec('84') ...
                     hex2dec('9c') hex2dec('60') hex2dec('a6') hex2dec('86') ...
                     hex2dec('b0') hex2dec('e1')]);

%% ---- load ----
if isempty(END_SEC)
    [y, fs] = audioread(WAV);
else
    info = audioinfo(WAV);
    fs = info.SampleRate;
    lo = max(1, round(START_SEC*fs) + 1);
    hi = min(info.TotalSamples, round(END_SEC*fs));
    [y, fs] = audioread(WAV, [lo hi]);
end
y = y(:, 1);
if fs ~= FS_EXPECT
    warning('%s: fs=%d, expected %d', WAV, fs, FS_EXPECT);
end
pos_offset = round(START_SEC*fs);   % 0-based absolute offset of y(1)
fprintf('=== s05b_header_locate_decode.m ===\n');
fprintf('%s\n%d samples @ %d Hz (%.2f s), searching from sample %d\n', ...
    WAV, numel(y), fs, numel(y)/fs, pos_offset);

tmpl_bits = [bits_lsb_first(FLAGS4), bits_lsb_first(HEADER_ADDR)];
tmpl_nrz  = 2*double(tmpl_bits) - 1;
template  = repelem(tmpl_nrz(:), SPS);
L = numel(template);
tmpl_c    = template - mean(template);
tmpl_norm = norm(tmpl_c);
assert(L == HEADER_LEN_SAMPLES, 'template length changed -- update HEADER_LEN_SAMPLES');

%% ---- full-file cross-correlation, both polarities ----
t_corr = tic;
cands = struct('pos', {}, 'z', {}, 'pol', {}, 'seg', {});
for pol = [+1, -1]
    z = ncc_zscore(pol*y, tmpl_c, tmpl_norm, L);
    % non-max suppression: repeatedly take the largest remaining peak above
    % Z_THRESH, then zero out a template-width neighbourhood around it
    zz = z;
    while true
        [zmax, imax] = max(zz);
        if zmax < Z_THRESH || ~isfinite(zmax)
            break
        end
        p = pos_offset + imax - 1;
        seg = floor(p / (SEG_LEN_SEC * fs));
        cands(end+1) = struct('pos', p, 'z', zmax, 'pol', pol, 'seg', seg); %#ok<SAGROW>
        lo_supp = max(1, imax - L);
        hi_supp = min(numel(zz), imax + L);
        zz(lo_supp:hi_supp) = -inf;
    end
end
fprintf('cross-correlation (%.2fs): %d raw candidate(s) above z=%.1f\n', ...
    toc(t_corr), numel(cands), Z_THRESH);

if isempty(cands)
    fprintf('No candidates found. Try lowering Z_THRESH, or check WAV/START_SEC/END_SEC.\n');
    return
end

[~, order] = sort([cands.z], 'descend');
cands = cands(order);
for k = 1:numel(cands)
    fprintf('  raw #%d  seg%03d  sample=%-10d  z=%-7.2f pol=%+d\n', ...
        k, cands(k).seg, cands(k).pos, cands(k).z, cands(k).pol);
end

%% ---- group raw candidates by (seg, polarity) -- one decode per group ----
seg_pol_keys = arrayfun(@(c) c.seg*10 + (c.pol>0), cands);   % pol packed into the key's last digit
[~, first_idx] = unique(seg_pol_keys, 'stable');   % cands is z-sorted desc, so 'stable' keeps the best per group
groups = cands(sort(first_idx));
[~, gorder] = sort([groups.z], 'descend');
groups = groups(gorder);
fprintf('\n%d group(s) (one decode attempt each): ', numel(groups));
for gi = 1:numel(groups)
    fprintf('seg%03d(%+d) ', groups(gi).seg, groups(gi).pol);
end
fprintf('\n');

%% ---- decode: one windowed Python call per (seg, pol) group ----
results = struct('seg', {}, 'pol', {}, 'z', {}, 'pos', {}, 'pass', {}, 'dest', {}, 'src', {});
for gi = 1:numel(groups)
    g = groups(gi);
    phase_guess = mod(g.pos, SPS);
    win_lo = g.pos - WINDOW_LEAD;
    win_hi = g.pos + WINDOW_TRAIL;
    seg_label = sprintf('seg%03d', g.seg);
    fprintf('\n---- decoding %s (pol %+d, z=%.2f, anchor sample=%d, window=[%d,%d]) ----\n', ...
        seg_label, g.pol, g.z, g.pos, win_lo, win_hi);

    pol_arg = 'norm'; if g.pol < 0, pol_arg = 'inv'; end
    cmd = sprintf(['"%s" "%s" "%s" --sps %.6f --phase %.4f --polarity %s ' ...
                   '--scan-phase %.2f --scan-phase-step %.3f ' ...
                   '--start-sample %d --end-sample %d --seg-label %s'], ...
        PYTHON_EXE, DECODE_SCRIPT, WAV, SPS, phase_guess, pol_arg, ...
        SCAN_PHASE, SCAN_PHASE_STEP, win_lo, win_hi, seg_label);
    [status, out] = system(cmd);
    fprintf('%s\n', out);

    r = struct('seg', g.seg, 'pol', g.pol, 'z', g.z, 'pos', g.pos, ...
        'pass', status == 0 && contains(out, 'CRC_PASS'), 'dest', '', 'src', '');
    if r.pass
        m = regexp(out, "Dest: '([^']*)'\s+Src: '([^']*)'", 'tokens', 'once');
        if ~isempty(m)
            r.dest = strtrim(m{1});
            r.src = strtrim(m{2});
        end
    end
    results(end+1) = r; %#ok<SAGROW>
end

%% ---- summary table ----
fprintf('\n===================== SUMMARY (%s) =====================\n', ...
    char(datetime('now')));
fprintf('%-8s %4s %8s %-12s %-6s %-8s %s\n', 'seg', 'pol', 'z', 'sample', 'CRC', 'Dest', 'Src');
fprintf('%s\n', repmat('-', 1, 70));
for k = 1:numel(results)
    r = results(k);
    crc_str = 'FAIL'; if r.pass, crc_str = 'PASS'; end
    fprintf('seg%03d   %+4d %8.2f %-12d %-6s %-8s %s\n', ...
        r.seg, r.pol, r.z, r.pos, crc_str, r.dest, r.src);
end
n_pass = sum([results.pass]);
fprintf('\n%d/%d groups CRC-passed.\n', n_pass, numel(results));
if n_pass < numel(results)
    fprintf(['Groups that did NOT pass are candidates for 05c_destuff_interactive.py ' ...
        '--seg N (crops then manual destuffs in one call; add --crop-only for just the wav).\n']);
end

%% ---- plot: waveform + z-score overlay, candidates marked with seg + pass/fail ----
% Both traces below are min/max ENVELOPE decimated for display only (the
% cross-correlation itself already ran on full resolution earlier) --
% plotting all ~37M raw points directly (as an earlier version of this
% script did) produces an unreadable solid block at this zoom level (no
% correlation peaks visible at all -- rasterized away) AND a ~995MB .fig
% file (MATLAB embeds the full plotted data). Min/max decimation keeps
% every real peak visible (a sharp spike always wins the min or max of
% whichever bucket it falls in, unlike naive stride subsampling which can
% alias a peak away entirely) while cutting the plotted point count from
% ~37M to ~2*N_BUCKETS.
N_BUCKETS = 8000;
figure('Name', 's05b header locate + decode', 'Position', [60 60 1600 750]);
t_axis = (pos_offset : pos_offset+numel(y)-1)';
main_pol = groups(1).pol;   % the polarity of the strongest group, for the z-trace display
[xw, yw] = minmax_decimate(t_axis, main_pol*y, N_BUCKETS);
yyaxis left;
plot(xw, yw, 'Color', [0.6 0.6 0.6], 'LineWidth', 0.4); hold on;
ylabel('amplitude (main-polarity-corrected)');
yyaxis right;
z_plot = ncc_zscore(main_pol*y, tmpl_c, tmpl_norm, L);
[xz, yz] = minmax_decimate(pos_offset + (0:numel(z_plot)-1)', z_plot, N_BUCKETS);
plot(xz, yz, 'Color', [0 0.447 0.741], 'LineWidth', 1.0);
yline(Z_THRESH, 'k--');
ylabel('correlation z-score');
yl = ylim;
for k = 1:numel(results)
    r = results(k);
    col = [0.85 0.325 0.098];               % orange = found, CRC did not pass
    if r.pass, col = [0.466 0.674 0.188]; end   % green = CRC passed
    if r.pol ~= main_pol, col = col * 0.6; end  % dim if opposite polarity from the main trace shown
    xline(r.pos, 'Color', col, 'LineWidth', 1.5);
    text(r.pos, yl(2), sprintf(' seg%03d%s', r.seg, ternary_str(r.pass, ' PASS', '')), ...
        'Color', col, 'FontSize', 8, 'FontWeight', 'bold', ...
        'Rotation', 90, 'VerticalAlignment', 'top', 'HorizontalAlignment', 'left');
end
grid on; xlabel('sample index (0-based, absolute)');
title(sprintf('%d seg group(s) (z>%.1f) -- green=CRC PASS, orange=found/no CRC -- %d/%d passed', ...
    numel(groups), Z_THRESH, n_pass, numel(results)));
zoom on;

[~, wav_stem] = fileparts(WAV);
fig_path = fullfile(fileparts(mfilename('fullpath')), 'Output', ...
    sprintf('s05b_locate_decode_%s.fig', wav_stem));
if ~isfolder(fileparts(fig_path)), mkdir(fileparts(fig_path)); end
savefig(gcf, fig_path);
png_path = strrep(fig_path, '.fig', '.png');
print(gcf, png_path, '-dpng', '-r150');
fprintf('\nsaved figure -> %s (+ .png)\n', fig_path);


%% ================= local functions =================
function [xd, yd] = minmax_decimate(x, y, n_buckets)
% Min/max envelope decimation for display -- see the plot section's comment
% for why this exists. Buckets y into n_buckets contiguous chunks and emits
% (min, max) per bucket, alternating so the line traces up/down through the
% envelope (the standard "min/max decimate" idiom, same rationale as
% 01_frame_detection/01a_waveform_power_overview.py's minmax_decimate).
% Uses each bucket's FIRST sample's x-coordinate for both emitted points --
% at n_buckets>=8000 over a multi-million-sample axis each bucket spans a
% negligible fraction of the plot width, so this doesn't visibly distort x.
n = numel(y);
n_buckets = min(n_buckets, n);
edges = round(linspace(1, n+1, n_buckets+1));
xd = nan(2*n_buckets, 1);
yd = nan(2*n_buckets, 1);
for i = 1:n_buckets
    lo = edges(i); hi = edges(i+1) - 1;
    if hi < lo, continue; end
    seg = y(lo:hi);
    xd(2*i-1) = x(lo); yd(2*i-1) = min(seg);
    xd(2*i)   = x(lo); yd(2*i)   = max(seg);
end
end

%% ================= local functions (unchanged from header_correlate_locate.m) =================
function bits = bits_lsb_first(bytes)
bits = zeros(1, numel(bytes)*8);
idx = 1;
for i = 1:numel(bytes)
    b = bytes(i);
    for k = 0:7
        bits(idx) = bitget(b, k+1);
        idx = idx + 1;
    end
end
end

function z = ncc_zscore(yw, tmpl_c, tmpl_norm, L)
n_valid = numel(yw) - L + 1;
num  = conv(yw, flipud(tmpl_c(:)), 'valid');
s1   = conv(yw,    ones(L,1), 'valid');
s2   = conv(yw.^2, ones(L,1), 'valid');
win_var_L = max(s2 - (s1.^2)/L, 0);
R = num ./ (tmpl_norm * sqrt(win_var_L) + 1e-12);
z = R / std(R);
if numel(z) ~= n_valid
    error('length mismatch: check conv sizing');
end
end

function s = ternary_str(cond, a, b)
if nargin < 3, b = ''; end
if cond, s = a; else, s = b; end
end
