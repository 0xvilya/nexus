using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

sealed class VoiceCapture : IDisposable
{
    public const int Seconds = 25;
    internal const int SampleRate = 16000;
    const int ByteRate = SampleRate * 2;
    const int Capacity = ByteRate * Seconds;
    const int BlockBytes = ByteRate / 5;
    const int BlockCount = Capacity / BlockBytes;
    const uint Done = 1; // WHDR_DONE
    IntPtr input, buffer, headers;
    int prepared, observed;
    readonly byte[] sampleWindow = new byte[BlockBytes];

    [StructLayout(LayoutKind.Sequential, Pack = 2)]
    struct Format
    {
        public ushort Tag, Channels;
        public uint SamplesPerSecond, BytesPerSecond;
        public ushort BlockAlign, BitsPerSample, ExtraSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Header
    {
        public IntPtr Data;
        public uint Length, Recorded;
        public UIntPtr User;
        public uint Flags, Loops;
        public IntPtr Next;
        public UIntPtr Reserved;
    }

    static readonly uint HeaderSize = (uint)Marshal.SizeOf(typeof(Header));

    [DllImport("winmm.dll")] static extern uint waveInOpen(out IntPtr handle, uint device, ref Format format, IntPtr callback, IntPtr instance, uint flags);
    [DllImport("winmm.dll")] static extern uint waveInPrepareHeader(IntPtr handle, IntPtr header, uint size);
    [DllImport("winmm.dll")] static extern uint waveInUnprepareHeader(IntPtr handle, IntPtr header, uint size);
    [DllImport("winmm.dll")] static extern uint waveInAddBuffer(IntPtr handle, IntPtr header, uint size);
    [DllImport("winmm.dll")] static extern uint waveInStart(IntPtr handle);
    [DllImport("winmm.dll")] static extern uint waveInReset(IntPtr handle);
    [DllImport("winmm.dll")] static extern uint waveInClose(IntPtr handle);
    [DllImport("winmm.dll", CharSet = CharSet.Unicode)] static extern uint waveInGetErrorText(uint code, StringBuilder message, uint length);

    public VoiceCapture()
    {
        var format = new Format {
            Tag = 1, Channels = 1, SamplesPerSecond = SampleRate,
            BytesPerSecond = ByteRate, BlockAlign = 2, BitsPerSample = 16
        };
        try
        {
            Check(waveInOpen(out input, uint.MaxValue, ref format, IntPtr.Zero, IntPtr.Zero, 0));
            buffer = Marshal.AllocHGlobal(Capacity);
            headers = Marshal.AllocHGlobal((int)HeaderSize * BlockCount);
            for (int block = 0; block < BlockCount; block++)
            {
                IntPtr header = BlockHeader(block);
                Marshal.StructureToPtr(new Header { Data = IntPtr.Add(buffer, block * BlockBytes), Length = BlockBytes }, header, false);
                Check(waveInPrepareHeader(input, header, HeaderSize));
                prepared++;
                Check(waveInAddBuffer(input, header, HeaderSize));
            }
            Check(waveInStart(input));
        }
        catch
        {
            Dispose();
            throw;
        }
    }

    IntPtr BlockHeader(int block) { return IntPtr.Add(headers, block * (int)HeaderSize); }
    Header ReadBlock(int block) { return (Header)Marshal.PtrToStructure(BlockHeader(block), typeof(Header)); }

    public bool SpeechEnded(VoiceEndpoint endpoint)
    {
        while (observed < BlockCount)
        {
            Header completed = ReadBlock(observed);
            if ((completed.Flags & Done) == 0) break;
            observed++;
            int count = (int)Math.Min(completed.Recorded, (uint)BlockBytes);
            if (count == 0) continue;
            Marshal.Copy(completed.Data, sampleWindow, 0, count);
            for (int offset = 0; offset + VoiceEndpoint.FrameBytes <= count; offset += VoiceEndpoint.FrameBytes)
                if (endpoint.Observe(sampleWindow, offset)) return true;
        }
        return false;
    }

    public byte[] Finish()
    {
        try
        {
            Check(waveInReset(input));
            var samples = new byte[Capacity];
            int count = 0;
            for (int block = 0; block < prepared; block++)
            {
                Header captured = ReadBlock(block);
                int bytes = (int)Math.Min(captured.Recorded, (uint)BlockBytes) & ~1;
                if (bytes == 0) continue;
                Marshal.Copy(captured.Data, samples, count, bytes);
                count += bytes;
            }
            if (count < ByteRate / 4)
                throw new InvalidOperationException("Запись слишком короткая. Попробуйте произнести фразу ещё раз.");
            double energy = 0;
            for (int i = 0; i < count; i += 2)
            {
                short sample = (short)(samples[i] | samples[i + 1] << 8);
                energy += (double)sample * sample;
            }
            if (Math.Sqrt(energy / (count / 2)) < 45)
                throw new InvalidOperationException("Речь не слышна. Проверьте выбранный микрофон и его громкость.");
            using (var stream = new MemoryStream(count + 44))
            using (var wave = new BinaryWriter(stream))
            {
                wave.Write(Encoding.ASCII.GetBytes("RIFF"));
                wave.Write(count + 36);
                wave.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
                wave.Write(16);
                wave.Write((ushort)1);
                wave.Write((ushort)1);
                wave.Write(SampleRate);
                wave.Write(ByteRate);
                wave.Write((ushort)2);
                wave.Write((ushort)16);
                wave.Write(Encoding.ASCII.GetBytes("data"));
                wave.Write(count);
                wave.Write(samples, 0, count);
                Array.Clear(samples, 0, samples.Length);
                return stream.ToArray();
            }
        }
        finally { Dispose(); }
    }

    public void Dispose()
    {
        Array.Clear(sampleWindow, 0, sampleWindow.Length);
        if (input != IntPtr.Zero)
        {
            waveInReset(input);
            // A driver can still own the memory when unprepare fails.
            // Keep the allocation alive rather than freeing memory in use.
            while (prepared > 0)
            {
                if (waveInUnprepareHeader(input, BlockHeader(prepared - 1), HeaderSize) != 0) return;
                prepared--;
            }
            if (waveInClose(input) != 0) return;
            input = IntPtr.Zero;
        }
        if (headers != IntPtr.Zero) { Marshal.FreeHGlobal(headers); headers = IntPtr.Zero; }
        if (buffer != IntPtr.Zero) { Marshal.FreeHGlobal(buffer); buffer = IntPtr.Zero; }
    }

    static void Check(uint result)
    {
        if (result == 0) return;
        var message = new StringBuilder(256);
        waveInGetErrorText(result, message, (uint)message.Capacity);
        throw new InvalidOperationException("Микрофон недоступен. Проверьте устройство ввода и разрешения Windows. " + message);
    }
}

// PCM endpoint detector: a brief burst cannot start a turn, and silence
// ends it only after enough speech. No model or microphone polling at rest.
sealed class VoiceEndpoint
{
    public const int FrameBytes = VoiceCapture.SampleRate * 2 / 50;
    int voicedFrames, quietFrames;
    public bool HasSpeech { get { return voicedFrames >= 8; } }
    public bool Observe(byte[] pcm, int offset)
    {
        double energy = 0;
        for (int i = offset; i < offset + FrameBytes; i += 2)
        {
            short sample = (short)(pcm[i] | pcm[i+1] << 8);
            energy += (double)sample * sample;
        }
        bool voiced = energy >= 150.0 * 150.0 * (FrameBytes / 2);
        if (voiced) { voicedFrames++; quietFrames = 0; }
        else if (++quietFrames > 45 && voicedFrames < 8) voicedFrames = 0;
        return voicedFrames >= 8 && quietFrames >= 45;
    }
}

static class LocalVoice
{
    static readonly string Folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "runtime", "voice");
    static readonly string Executable = Path.Combine(Folder, "whisper-cli.exe");
    static readonly string Model = Path.Combine(Folder, "ggml-base-q5_1.bin");

    public static bool Available { get { return File.Exists(Executable) && File.Exists(Model); } }

    public static string Transcribe(byte[] wave, string language, CancellationToken token, bool quick = false)
    {
        if (!Available) throw new InvalidOperationException("Распознавание речи отсутствует в этой сборке. Обновите Nexus.");
        if (wave == null || wave.Length < 44 || wave.Length > 2 * 1024 * 1024)
            throw new InvalidOperationException("Недопустимый размер записи.");
        if (language != "ru" && language != "en") language = "auto";
        string args = "-m \"" + Model + "\" -f - -of nexus-stdin -l " + language +
            " -ng -np -nt -sns -t " + Math.Max(1, Math.Min(4, Environment.ProcessorCount));
        if (quick) args += " -bo 1 -bs 1 -nf";
        var info = new ProcessStartInfo(Executable, args) {
            WorkingDirectory = Folder, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
        };
        token.ThrowIfCancellationRequested();
        using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
        using (var process = Process.Start(info))
        {
            deadline.CancelAfter(TimeSpan.FromSeconds(90));
            using (deadline.Token.Register(() => { try { if (!process.HasExited) process.Kill(); } catch (InvalidOperationException) {} catch (System.ComponentModel.Win32Exception) {} }))
            {
                var output = process.StandardOutput.ReadToEndAsync();
                var errors = process.StandardError.ReadToEndAsync();
                try
                {
                    var input = process.StandardInput.BaseStream;
                    input.Write(wave, 0, wave.Length);
                    input.Close();
                }
                catch (IOException) { /* The process may have been cancelled or rejected the stream. */ }
                process.WaitForExit();
                Task.WaitAll(output, errors);
                token.ThrowIfCancellationRequested();
                if (deadline.IsCancellationRequested)
                    throw new TimeoutException("Локальное распознавание не завершилось вовремя.");
                if (process.ExitCode != 0 || output.Result.Length > 12000)
                    throw new InvalidOperationException("Не удалось распознать запись. Проверьте установку Nexus и повторите.");
                return output.Result.Trim();
            }
        }
    }
}
