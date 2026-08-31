#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Not titled yet
# Description: Step 1 (05a): decode a cs16 IQ capture to 48k wav, saving a periodic spectrogram PNG every 30s of newly-arrived IQ for candidate-frame scanning.
# GNU Radio version: 3.10.12.0

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import analog
import math
from gnuradio import blocks
import pmt
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
import gr_plot_capture
import sip
import threading


def snipfcn_snippet_plot(self):
    # NOTE: inside a Snippet the flowgraph object is bound to `self`,
    # not `tb` -- GRC generates `def snipfcn_<name>(self)`.
    # Safety net: flush whatever partial segment remains if the window gets
    # closed before the file runs out on its own (start_periodic_plot's
    # own stall-detection already handles the natural end-of-file case).
    self.plotcap_iq.stop_periodic_plot(flush_partial=True)
    # plotcap_af is currently disabled on the canvas -- calling a method on
    # a disabled block AttributeErrors, since GRC never instantiates it.
    # self.plotcap_af.stop_periodic_plot(flush_partial=True)

def snipfcn_snippet_start_periodic(self):
    # NOTE: inside a Snippet the flowgraph object is bound to `self`,
    # not `tb` -- GRC generates `def snipfcn_<name>(self)`.
    # One PNG every 30s of newly-arrived IQ, until the file runs out.
    self.plotcap_iq.start_periodic_plot(30)


def snippets_main_after_start(tb):
    snipfcn_snippet_start_periodic(tb)

def snippets_main_after_stop(tb):
    snipfcn_snippet_plot(tb)

class IQtoOgg_plot(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Not titled yet", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Not titled yet")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "IQtoOgg_plot")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.samp_rate = samp_rate = 100000
        self.cs16_path = cs16_path = r"D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Data\20260723_091639_100000_98266_x.cs16"
        self.variable_low_pass_filter_taps_0_2 = variable_low_pass_filter_taps_0_2 = firdes.low_pass(1.0, samp_rate, 15000, 1000, window.WIN_HAMMING, 6.76)
        self.rec_stem = rec_stem = "_".join(cs16_path.replace("\\", "/").rsplit("/", 1)[-1].split("_")[:2])
        self.n_capture = n_capture = 60*100000

        ##################################################
        # Blocks
        ##################################################

        self.rational_resampler_xxx_0 = filter.rational_resampler_fff(
                interpolation=12,
                decimation=25,
                taps=[],
                fractional_bw=0)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_f(
            1024, #size
            samp_rate, #samp_rate
            "", #name
            1, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0.set_y_axis(-1, 1)

        self.qtgui_time_sink_x_0.set_y_label('Amplitude', "")

        self.qtgui_time_sink_x_0.enable_tags(True)
        self.qtgui_time_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.qtgui_time_sink_x_0.enable_autoscale(False)
        self.qtgui_time_sink_x_0.enable_grid(False)
        self.qtgui_time_sink_x_0.enable_axis_labels(True)
        self.qtgui_time_sink_x_0.enable_control_panel(False)
        self.qtgui_time_sink_x_0.enable_stem_plot(False)


        labels = ['Signal 1', 'Signal 2', 'Signal 3', 'Signal 4', 'Signal 5',
            'Signal 6', 'Signal 7', 'Signal 8', 'Signal 9', 'Signal 10']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['blue', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]
        styles = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        markers = [-1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1]


        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_time_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_time_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_time_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_time_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_time_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_time_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_time_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_time_sink_x_0_win = sip.wrapinstance(self.qtgui_time_sink_x_0.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_time_sink_x_0_win)
        self.plotcap_iq = gr_plot_capture.plot_capture(
            dtype='complex', samp_rate=samp_rate, nsamples=n_capture,
            skip=0, mode='spec', sps=0, nfft=1024,
            out_dir='Figure', prefix=rec_stem, title='',
            save_npy=False)
        self.freq_xlating_fir_filter_xxx_0_0_0 = filter.freq_xlating_fir_filter_ccf(1, variable_low_pass_filter_taps_0_2, 0, samp_rate)
        self.blocks_wavfile_sink_0 = blocks.wavfile_sink(
            "Output/" + rec_stem + "_48k.wav",
            1,
            48000,
            blocks.FORMAT_WAV,
            blocks.FORMAT_FLOAT,
            False
            )
        self.blocks_throttle2_1 = blocks.throttle( gr.sizeof_gr_complex*1, samp_rate, True, 0 if "auto" == "auto" else max( int(float(0.1) * samp_rate) if "auto" == "time" else int(0.1), 1) )
        self.blocks_interleaved_short_to_complex_0 = blocks.interleaved_short_to_complex(False, False,1.0)
        self.blocks_file_source_0 = blocks.file_source(gr.sizeof_short*1, cs16_path, False, 0, 0)
        self.blocks_file_source_0.set_begin_tag(pmt.PMT_NIL)
        self.analog_quadrature_demod_cf_0_1_0 = analog.quadrature_demod_cf((15.91*2))


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_quadrature_demod_cf_0_1_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.analog_quadrature_demod_cf_0_1_0, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.blocks_file_source_0, 0), (self.blocks_interleaved_short_to_complex_0, 0))
        self.connect((self.blocks_interleaved_short_to_complex_0, 0), (self.blocks_throttle2_1, 0))
        self.connect((self.blocks_throttle2_1, 0), (self.freq_xlating_fir_filter_xxx_0_0_0, 0))
        self.connect((self.blocks_throttle2_1, 0), (self.plotcap_iq, 0))
        self.connect((self.freq_xlating_fir_filter_xxx_0_0_0, 0), (self.analog_quadrature_demod_cf_0_1_0, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.blocks_wavfile_sink_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "IQtoOgg_plot")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        snippets_main_after_stop(self)
        event.accept()

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_variable_low_pass_filter_taps_0_2(firdes.low_pass(1.0, self.samp_rate, 15000, 1000, window.WIN_HAMMING, 6.76))
        self.blocks_throttle2_1.set_sample_rate(self.samp_rate)
        self.qtgui_time_sink_x_0.set_samp_rate(self.samp_rate)

    def get_cs16_path(self):
        return self.cs16_path

    def set_cs16_path(self, cs16_path):
        self.cs16_path = cs16_path
        self.blocks_file_source_0.open(self.cs16_path, False)

    def get_variable_low_pass_filter_taps_0_2(self):
        return self.variable_low_pass_filter_taps_0_2

    def set_variable_low_pass_filter_taps_0_2(self, variable_low_pass_filter_taps_0_2):
        self.variable_low_pass_filter_taps_0_2 = variable_low_pass_filter_taps_0_2
        self.freq_xlating_fir_filter_xxx_0_0_0.set_taps(self.variable_low_pass_filter_taps_0_2)

    def get_rec_stem(self):
        return self.rec_stem

    def set_rec_stem(self, rec_stem):
        self.rec_stem = rec_stem
        self.blocks_wavfile_sink_0.open("Output/" + self.rec_stem + "_48k.wav")

    def get_n_capture(self):
        return self.n_capture

    def set_n_capture(self, n_capture):
        self.n_capture = n_capture




def main(top_block_cls=IQtoOgg_plot, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()

    tb.start()
    tb.flowgraph_started.set()
    snippets_main_after_start(tb)
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        snippets_main_after_stop(tb)
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
