import queue
import threading
import time
import unittest
from array import array

from radiostudio.services.broadcast import (
    ENCODED_CHUNK_BYTES, FRAME_MS, PCM_BYTES_PER_FRAME, EncoderOutput, PCMBuffer,
    BroadcastManager as BaseBroadcastManager, encoded_queue_capacity, reconnect_delay,
)
from radiostudio.services.broadcast_quality import MIX_PEAK, BroadcastManager, limit_mixed_samples


class _DrainRacingQueue(queue.Queue):
    def __init__(self):
        super().__init__(maxsize=1)
        self.simulate_drain_after_full = False

    def put_nowait(self, item):
        if self.simulate_drain_after_full:
            self.simulate_drain_after_full = False
            super().get_nowait()
            raise queue.Full
        return super().put_nowait(item)


class _FailingSink:
    def publish(self, _data):
        raise OSError("socket failed")


class _CollectingSink:
    def __init__(self):
        self.chunks = []
        self.first_chunk = threading.Event()

    def publish(self, data):
        self.chunks.append(data)
        self.first_chunk.set()


class _EncodedPipe:
    def __init__(self, data):
        self.data = data

    def read(self, _size):
        data, self.data = self.data, b""
        return data


class _FinishedProcess:
    stdout = _EncodedPipe(b"encoded mp3")

    def poll(self):
        return 0


class _ActiveProcess:
    def __init__(self):
        self.terminated = False
        self.stdout = _EncodedPipe(b"encoded mp3")

    def poll(self):
        return -15 if self.terminated else None

    def terminate(self):
        self.terminated = True


class _StopAfterOneFrame(threading.Event):
    def wait(self, timeout=None):
        self.set()
        return True


class _BrokenBuffer:
    def take(self, _size):
        raise RuntimeError("buffer de teste")


class BroadcastQualityTests(unittest.TestCase):
    def test_mixer_isolates_bad_source_and_output(self):
        for manager_type in (BaseBroadcastManager, BroadcastManager):
            with self.subTest(manager=manager_type.__name__):
                manager = manager_type()
                manager.stop_event = _StopAfterOneFrame()
                good_buffer = PCMBuffer()
                good_buffer.min_prebuffer_bytes = 4
                good_buffer.append(b"\x01\x00\x01\x00")
                manager.sources = {"broken": (_BrokenBuffer(), 1.0),
                                   "main": (good_buffer, 1.0)}

                failed_output = EncoderOutput("saída com falha", 128, destination=object())
                failed_output.status = "Ativo"
                failed_output.feed = lambda _pcm: (_ for _ in ()).throw(RuntimeError("encoder"))
                working_output = EncoderOutput("saída estável", 128, destination=object())
                working_output.status = "Ativo"
                received = []
                working_output.feed = received.append
                manager.outputs = [failed_output, working_output]

                with self.assertLogs("radiostudio", level="ERROR"):
                    manager._mix_loop()

                self.assertNotIn("broken", manager.sources)
                self.assertTrue(failed_output.connection_failed.is_set())
                self.assertEqual(len(received), 1)
                samples = array("h")
                samples.frombytes(received[0])
                self.assertNotEqual(max(map(abs, samples)), 0)

    def test_reconnect_delay_grows_and_stays_bounded(self):
        self.assertEqual([reconnect_delay(attempt) for attempt in range(1, 6)],
                         [2.0, 4.0, 8.0, 16.0, 30.0])
        self.assertEqual(reconnect_delay(100), 30.0)

    def test_pcm_buffer_prebuffers_two_frames_before_playing(self):
        buffer = PCMBuffer()
        chunk = b"\x01\x00\x02\x00" * (buffer.min_prebuffer_bytes // 4 // 2)
        buffer.append(chunk)

        self.assertEqual(buffer.take(buffer.min_prebuffer_bytes // 2),
                         b"\0" * (buffer.min_prebuffer_bytes // 2))
        buffer.append(chunk)

        first_audio = buffer.take(buffer.min_prebuffer_bytes // 2)
        self.assertEqual(first_audio, chunk)
        self.assertEqual(buffer.diagnostics_snapshot()["warmup_silence_frames"], 1)
        self.assertEqual(buffer.diagnostics_snapshot()["underflow_frames"], 0)

    def test_pcm_buffer_counts_underrun_and_zero_fill(self):
        buffer = PCMBuffer()
        buffer.min_prebuffer_bytes = 4
        self.assertEqual(buffer.take(8), b"\0" * 8)
        buffer.append(b"\x01\x00\x02\x00")
        self.assertEqual(buffer.take(8), b"\x01\x00\x02\x00" + b"\0" * 4)

        diagnostics = buffer.diagnostics_snapshot()
        self.assertEqual(diagnostics["warmup_silence_frames"], 1)
        self.assertEqual(diagnostics["underflow_frames"], 1)
        self.assertEqual(diagnostics["zero_filled_bytes"], 4)

    def test_pcm_buffer_resumes_on_first_new_samples_after_an_underrun(self):
        buffer = PCMBuffer()
        buffer.min_prebuffer_bytes = 8
        initial = b"\x01\x00\x01\x00\x02\x00\x02\x00"
        resumed = b"\x03\x00\x03\x00"
        buffer.append(initial)

        self.assertEqual(buffer.take(4), initial[:4])
        self.assertEqual(buffer.take(8), initial[4:] + b"\0" * 4)
        buffer.append(resumed)

        self.assertEqual(buffer.take(4), resumed)

    def test_pcm_buffer_transition_flush_keeps_steady_state_priming(self):
        buffer = PCMBuffer()
        buffer.min_prebuffer_bytes = 8
        buffer.append(b"\x01\x00\x01\x00" * 2)
        self.assertEqual(buffer.take(4), b"\x01\x00\x01\x00")
        buffer.append(b"\x02\x00\x02\x00")

        buffer.clear(preserve_primed=True)
        buffer.append(b"\x03\x00\x03\x00")

        self.assertEqual(buffer.take(4), b"\x03\x00\x03\x00")
        self.assertEqual(buffer.diagnostics_snapshot()["buffer_ms"], 0)

    def test_oversized_pcm_append_keeps_the_newest_complete_audio(self):
        buffer = PCMBuffer()
        frame_bytes = b"\x01\x00\x02\x00"
        data = b"\0" * buffer.capacity_bytes + frame_bytes

        buffer.append(data)

        self.assertEqual(buffer.length, buffer.capacity_bytes)
        self.assertEqual(buffer.take(buffer.capacity_bytes)[-4:], frame_bytes)
        self.assertEqual(buffer.diagnostics_snapshot()["dropped_bytes"], 4)

    def test_encoder_pcm_queue_reports_dropped_frames(self):
        output = EncoderOutput("test", 128, destination=object())
        output.status = "Ativo"
        for value in range(output.queue.maxsize + 1):
            output.feed(bytes([value % 256]))

        self.assertEqual(output.dropped_pcm_frames, 1)
        self.assertEqual(output.max_pcm_queue_depth, output.queue.maxsize)
        self.assertEqual(output.queue.get_nowait(), b"\x01")

    def test_encoder_retries_frame_if_consumer_drains_during_full_queue_race(self):
        output = EncoderOutput("test", 128, destination=object())
        output.status = "Ativo"
        output.queue = _DrainRacingQueue()
        output.queue.put_nowait(b"consumed")
        output.queue.simulate_drain_after_full = True

        output.feed(b"new frame")

        self.assertEqual(output.queue.get_nowait(), b"new frame")
        self.assertEqual(output.dropped_pcm_frames, 0)

    def test_encoder_does_not_queue_pcm_while_destination_is_connecting(self):
        output = EncoderOutput("teste", 128, destination=object())
        output.status = "Conectando"

        output.feed(b"stale frame")

        self.assertTrue(output.queue.empty())
        self.assertEqual(output.dropped_pcm_frames, 1)

    def test_audio_diagnostics_include_mixer_and_output_metrics(self):
        manager = BroadcastManager()
        output = EncoderOutput("teste", 128, destination=object())
        manager.outputs.append(output)
        manager.push_audio("main", b"\0" * 8)
        output.network_queue.put_nowait(b"mp3")
        output.max_network_queue_depth = 2
        output.dropped_encoded_chunks = 1

        diagnostics = manager.diagnostics_snapshot()

        self.assertIn("mixer", diagnostics)
        self.assertIn("main", diagnostics["sources"])
        self.assertEqual(diagnostics["outputs"][0]["name"], "teste")
        self.assertEqual(diagnostics["outputs"][0]["encoded_queue_ms"], 64.0)
        self.assertEqual(diagnostics["outputs"][0]["encoded_queue_high_watermark_ms"], 128.0)
        self.assertEqual(diagnostics["outputs"][0]["dropped_encoded_chunks"], 1)

    def test_promoting_mix_discards_outgoing_pcm_and_keeps_incoming_buffer(self):
        manager = BroadcastManager()
        outgoing = PCMBuffer()
        incoming = PCMBuffer()
        outgoing.append(b"\x01\x00\x01\x00")
        incoming.append(b"\x02\x00\x02\x00")
        manager.sources = {"main": (outgoing, 1.0), "mix": (incoming, 0.5)}

        manager.promote_source("mix", "main")

        self.assertEqual(manager.sources, {"main": (incoming, 0.5)})

    def test_full_encoded_queue_requests_bounded_reconnect(self):
        output = EncoderOutput("teste", 128, destination=object())
        output.network_queue = queue.Queue(maxsize=1)
        process = _ActiveProcess()
        self.assertTrue(output._enqueue_encoded(b"chunk-1"))
        self.assertFalse(output._enqueue_encoded(b"chunk-2", process=process))

        self.assertTrue(output.connection_failed.is_set())
        self.assertTrue(process.terminated)
        self.assertEqual(output.dropped_encoded_chunks, 1)
        self.assertEqual(output.max_network_queue_depth, 1)
        output._discard_queued_encoded()
        self.assertEqual(output.dropped_encoded_chunks, 2)
        self.assertTrue(output.network_queue.empty())

    def test_encoded_queue_capacity_stays_within_two_seconds_across_bitrates(self):
        for bitrate in (32, 128, 192, 256, 320):
            with self.subTest(bitrate=bitrate):
                capacity = encoded_queue_capacity(bitrate)
                buffered_ms = capacity * ENCODED_CHUNK_BYTES * 8 / bitrate
                self.assertLessEqual(buffered_ms, 2000)

    def test_network_write_failure_counts_chunk_and_requests_reconnect(self):
        output = EncoderOutput("teste", 128, destination=_FailingSink())
        output.network_queue.put_nowait(b"chunk")
        process = _ActiveProcess()

        output._publish_encoded(process=process, network_queue=output.network_queue)

        self.assertTrue(output.connection_failed.is_set())
        self.assertTrue(process.terminated)
        self.assertEqual(output.dropped_encoded_chunks, 1)

    def test_network_backpressure_terminates_encoder_to_unblock_pcm_writer(self):
        output = EncoderOutput("teste", 128, destination=object())
        network_queue = queue.Queue(maxsize=1)
        network_queue.put_nowait(b"stalled chunk")
        process = _ActiveProcess()

        output._read_encoded(process, network_queue)

        self.assertTrue(output.connection_failed.is_set())
        self.assertTrue(process.terminated)
        self.assertEqual(output.dropped_encoded_chunks, 1)

    def test_encoded_reader_and_network_writer_transfer_chunks_independently(self):
        sink = _CollectingSink()
        output = EncoderOutput("teste", 128, destination=sink)
        process = _FinishedProcess()
        network_queue = queue.Queue(maxsize=2)

        output._read_encoded(process, network_queue)
        output._publish_encoded(process, network_queue)

        self.assertEqual(sink.chunks, [b"encoded mp3"])

    def test_live_encoder_emits_mp3_before_input_stream_ends(self):
        sink = _CollectingSink()
        output = EncoderOutput("teste", 192, destination=sink)
        output.start()
        try:
            deadline = time.monotonic() + 5
            while output.status != "Ativo" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(output.status, "Ativo", output.error)

            pcm = b"\x00\x20\x00\x20\x00\xe0\xff\xe0" * (PCM_BYTES_PER_FRAME // 8)
            for _ in range(120):
                output.feed(pcm)
                if sink.first_chunk.is_set():
                    break
                time.sleep(FRAME_MS / 1000)

            self.assertTrue(sink.first_chunk.wait(0.5),
                            "FFmpeg kept buffering the live stream until EOF")
            self.assertTrue(sink.chunks[0].startswith(b"\xff"))
        finally:
            output.stop()
            if output.thread:
                output.thread.join(timeout=3)
            self.assertFalse(output.thread and output.thread.is_alive())

    def test_limiter_scales_the_whole_frame_instead_of_hard_clipping_peaks(self):
        pcm, gain = limit_mixed_samples([40000, -20000], 1.0)
        samples = array("h")
        samples.frombytes(pcm)

        self.assertLess(gain, 1.0)
        self.assertLessEqual(max(map(abs, samples)), MIX_PEAK)
        self.assertAlmostEqual(samples[1] / samples[0], -0.5, places=2)

    def test_limiter_releases_gradually_after_an_overload(self):
        _pcm, reduced = limit_mixed_samples([50000, -50000], 1.0)
        _pcm, recovered = limit_mixed_samples([1000, -1000], reduced)

        self.assertGreater(recovered, reduced)
        self.assertLess(recovered, 1.0)


if __name__ == "__main__":
    unittest.main()
