% header_correlate_locate.m -- FINE packet localization via header
% cross-correlation, using power_dip_locate.m's dip position only as a
% COARSE starting window to search around (power = coarse, header = fine).
%
% Method, ported from 01_frame_detection/01_frame_detection.py's
% detect_frame_starts(): build an NRZ template from the 4 leading HDLC
% flags (0x7E x4) + the 14-byte Dest+Src address (fixed protocol content,
% unaffected by which telemetry the frame carries), upsample by SPS, and
% slide it across the waveform computing a normalized correlation. The peak
% marks where the flags+address actually sit.
%
% ONE DELIBERATE DIFFERENCE from 01_frame_detection.py: that script
% correlates the RAW dot product (numerator = sum(window.*template), only
% normalized by the two vectors' norms) against baseline-RESTORED audio
% (scionx.baseline.restore_baseline strips DC first). This script instead
% uses a PEARSON-style correlation -- the template is made zero-mean before
% correlating, which removes sensitivity to a DC offset in the window
% without needing a separate baseline-restoration pass:
%   sum((y - mean(y)) .* (t - mean(t)))
% Because template_c is already zero-mean, sum(template_c) = 0, so the
% mean(y) term drops out of the numerator algebraically -- only the
% denominator needs the window's own variance. This matters here because
% seg010 shows a visible DC step mid-segment (see eye_fixed_grid.m's
% bottom-right panel); a raw dot product would be biased by that.
%
% Validated the same way as power_dip_locate.m: check the peak position
% against seg017/seg002's already-known content-start (from the Python
% fixed-grid HDLC decode) BEFORE trusting it on seg010/seg007. The
% correlation peak lands at the START of the leading flags, not the frame
% content -- so this script also reports the calibrated flags-to-content
% offset from the two known-good cases, and applies it to seg010/seg007.

OUT_DIR = 'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Output\';
FS_EXPECT = 48000;
SPS = 5;
SEARCH_MARGIN = 10000;   % samples either side of the coarse (power-dip) candidate

% -- template: 4x flag (0x7E) + 14-byte Dest+Src address, LSB-first bits --
FLAGS4      = uint8([0x7E 0x7E 0x7E 0x7E]);
HEADER_ADDR = uint8([hex2dec('84') hex2dec('9c') hex2dec('60') hex2dec('86') ...
                     hex2dec('aa') hex2dec('40') hex2dec('60') hex2dec('84') ...
                     hex2dec('9c') hex2dec('60') hex2dec('a6') hex2dec('86') ...
                     hex2dec('b0') hex2dec('e1')]);
tmpl_bits = [bits_lsb_first(FLAGS4), bits_lsb_first(HEADER_ADDR)];
tmpl_nrz  = 2*double(tmpl_bits) - 1;
template  = repelem(tmpl_nrz(:), SPS);         % upsample -> 144*5 = 720 samples
L = numel(template);
tmpl_c    = template - mean(template);         % zero-mean once, reused every window
tmpl_norm = norm(tmpl_c);

% Coarse candidates: power_dip_locate.m's half-frame-corrected dip centers
% (content-start estimate). known_start = independently-confirmed content
% start (Python fixed-grid HDLC decode), NaN where there is none.
segments = struct( ...
    'name',        {'seg017 (known-good)', 'seg002 (known-good)', 'seg010', 'seg007'}, ...
    'file',        {'20260723_091639_seg017_510-540s.wav', ...
                     '20260723_091639_seg002_60-90s.wav', ...
                     '20260723_091639_seg010_300-330s.wav', ...
                     '20260720_220206_seg007_210-240s.wav'}, ...
    'coarse_cand', {916579, 1159459, 324259, 1383139}, ...
    'known_start', {916413, 1159245, NaN, NaN});

figure('Name', 'header correlation -- fine packet localization', 'Position', [60 60 1500 900]);
flag_to_content_offsets = [];   % collect from known-good cases to calibrate

for k = 1:numel(segments)
    seg = segments(k);
    [y, fs] = audioread([OUT_DIR seg.file]);
    y = y(:, 1);
    if fs ~= FS_EXPECT
        warning('%s: fs=%d, expected %d', seg.file, fs, FS_EXPECT);
    end

    lo = max(1, seg.coarse_cand - SEARCH_MARGIN);
    hi = min(numel(y), seg.coarse_cand + SEARCH_MARGIN + L);
    yw = y(lo:hi);

    % try both polarities (FM-demod 0/1 mapping can flip); keep whichever
    % gives the stronger peak
    best = [];
    for pol = [+1, -1]
        [z, peak_idx] = ncc_zscore(pol*yw, tmpl_c, tmpl_norm, L);
        if isempty(best) || max(z) > best.zmax
            best = struct('pol', pol, 'z', z, 'peak_idx', peak_idx, 'zmax', max(z));
        end
    end
    header_start = lo + best.peak_idx - 1;   % absolute sample index, start of the flags

    fprintf('%-22s header peak: sample=%d  z=%.2f  polarity=%+d', ...
        seg.name, header_start, best.zmax, best.pol);
    if ~isnan(seg.known_start)
        off = seg.known_start - header_start;   % flags-start -> content-start offset
        flag_to_content_offsets(end+1) = off; %#ok<SAGROW>
        fprintf('   | known content-start=%d  flags->content offset=%+d samples (%.1f symbols)\n', ...
            seg.known_start, off, off/SPS);
    else
        fprintf('   | no ground truth\n');
    end

    subplot(2, 2, k);
    t_axis = (lo : lo+numel(best.z)-1)' - 1;    % 0-based sample index per correlation position
    yyaxis left;
    plot((lo:hi)'-1, best.pol*yw, 'Color', [0.6 0.6 0.6], 'LineWidth', 0.4); hold on;
    xline(header_start-1, 'Color', [0.85 0.325 0.098], 'LineWidth', 2);
    xline(header_start-1+L, 'Color', [0.85 0.325 0.098], 'LineWidth', 1, 'LineStyle', '--');
    patch([header_start-1, header_start-1+L, header_start-1+L, header_start-1], ...
          [min(ylim) min(ylim) max(ylim) max(ylim)], [0.85 0.325 0.098], ...
          'FaceAlpha', 0.08, 'EdgeColor', 'none');
    ylabel('amplitude (polarity-corrected)');
    if ~isnan(seg.known_start)
        xline(seg.known_start-1, 'Color', [0.466 0.674 0.188], 'LineWidth', 2, 'LineStyle', ':');
    end
    yyaxis right;
    plot(t_axis, best.z, 'Color', [0 0.447 0.741], 'LineWidth', 1.2);
    ylabel('correlation z-score');
    grid on; xlabel('sample index');
    title(sprintf('%s -- header z=%.1f @ sample %d', seg.name, best.zmax, header_start), ...
        'Interpreter', 'none');
end

if ~isempty(flag_to_content_offsets)
    fprintf('\ncalibrated flags->content offset: mean=%.1f samples (%.2f symbols), values=%s\n', ...
        mean(flag_to_content_offsets), mean(flag_to_content_offsets)/SPS, ...
        mat2str(flag_to_content_offsets));
end

sgtitle({'Header (4 flags + 14-byte address) cross-correlation -- fine localization', ...
         'orange solid = header start, orange dashed = header end, green dotted = known-good content start'}, ...
        'FontWeight', 'bold', 'FontSize', 11);

fig_path = [OUT_DIR 'header_correlate_locate.fig'];
savefig(gcf, fig_path);
fprintf('saved figure -> %s\n', fig_path);


%% ---- local functions ----
function bits = bits_lsb_first(bytes)
% LSB-first bit serialization, matching 01_frame_detection.py's
% bits_lsb_first / the AX.25 on-wire order confirmed by hdlc_bitorder_test.grc.
bits = zeros(1, numel(bytes)*8);
idx = 1;
for i = 1:numel(bytes)
    b = bytes(i);
    for k = 0:7
        bits(idx) = bitget(b, k+1);   % bitget(.,1) = LSB
        idx = idx + 1;
    end
end
end

function [z, peak_idx] = ncc_zscore(yw, tmpl_c, tmpl_norm, L)
% Pearson-style normalized cross-correlation z-score across all valid
% offsets of yw. See file header for why the template alone is
% zero-meaned (removes DC sensitivity without a separate baseline pass).
n_valid = numel(yw) - L + 1;
num  = conv(yw, flipud(tmpl_c(:)), 'valid');           % sum(y(n:n+L-1) .* tmpl_c)
s1   = conv(yw,    ones(L,1), 'valid');                % sliding sum
s2   = conv(yw.^2, ones(L,1), 'valid');                % sliding sum of squares
win_var_L = max(s2 - (s1.^2)/L, 0);                    % L * window variance
R = num ./ (tmpl_norm * sqrt(win_var_L) + 1e-12);
z = R / std(R);
[~, peak_idx] = max(z);
if numel(z) ~= n_valid
    error('length mismatch: check conv sizing');
end
end
