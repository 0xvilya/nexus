#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::{io::{BufRead, BufReader, Write}, path::PathBuf, process::{Child, ChildStdin, Command, Stdio}, sync::{mpsc, Mutex}, time::Duration};
use tauri::{Manager, State};

struct WorkerProcess { child: Child, input: ChildStdin, responses: mpsc::Receiver<Result<String, String>> }

impl Drop for WorkerProcess {
    fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); }
}

struct Bridge { worker: Mutex<Option<WorkerProcess>>, data_dir: PathBuf, runtime_dir: PathBuf }

impl Bridge {
    fn spawn(&self) -> Result<WorkerProcess, String> {
        let mut command;
        if cfg!(debug_assertions) {
            let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
            command = Command::new(std::env::var("RAINFORGE_PYTHON").unwrap_or_else(|_| "python".into()));
            command.arg(repo.join("scripts/worker.py"));
        } else {
            let folder = std::env::current_exe().map_err(|e| e.to_string())?.parent().ok_or("Executable directory missing")?.to_owned();
            command = Command::new(folder.join("rainforge-worker.exe"));
        }
        command.arg("--data-dir").arg(&self.data_dir).arg("--runtime-dir").arg(&self.runtime_dir)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        if !cfg!(debug_assertions) { command.arg("--models-dir").arg(self.data_dir.join("models")); }
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().map_err(|e| format!("Cannot start the local worker: {e}"))?;
        let input = child.stdin.take().ok_or("Worker stdin unavailable")?;
        let output = child.stdout.take().ok_or("Worker stdout unavailable")?;
        let (send, receive) = mpsc::sync_channel(8);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(output);
            loop {
                let mut line = Vec::new();
                // Responses are bounded before allocating an untrusted line.
                use std::io::Read;
                match reader.by_ref().take(2 * 1024 * 1024 + 1).read_until(b'\n', &mut line) {
                    Ok(0) => { let _ = send.send(Err("Worker exited".into())); break; },
                    Ok(_) if line.len() > 2 * 1024 * 1024 => { let _ = send.send(Err("Worker response limit exceeded".into())); break; },
                    Ok(_) => { if send.send(String::from_utf8(line).map_err(|e| e.to_string())).is_err() { break; } },
                    Err(e) => { let _ = send.send(Err(e.to_string())); break; }
                }
            }
        });
        Ok(WorkerProcess { child, input, responses: receive })
    }

    fn request(&self, message: Value) -> Result<Value, String> {
        let serialized = serde_json::to_vec(&message).map_err(|e| e.to_string())?;
        if serialized.len() > 128 * 1024 { return Err("Request limit exceeded".into()); }
        let mut slot = self.worker.lock().map_err(|_| "Worker lock failed")?;
        if slot.is_none() { *slot = Some(self.spawn()?); }
        let worker = slot.as_mut().ok_or("Worker unavailable")?;
        let result = (|| {
            worker.input.write_all(&serialized).and_then(|_| worker.input.write_all(b"\n")).and_then(|_| worker.input.flush()).map_err(|e| e.to_string())?;
            let line = worker.responses.recv_timeout(Duration::from_secs(75)).map_err(|_| "Worker response timeout or disconnect")??;
            let response: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
            if response.get("id") != message.get("id") { return Err("Worker response ID mismatch".into()); }
            Ok(response)
        })();
        if result.is_err() { *slot = None; }
        result
    }
}

#[tauri::command]
async fn rpc(bridge: State<'_, std::sync::Arc<Bridge>>, message: Value) -> Result<Value, String> {
    let handle = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || handle.request(message)).await.map_err(|e| e.to_string())?
}

fn main() {
    tauri::Builder::default().setup(|app| {
        let data_dir = app.path().app_local_data_dir()?;
        let runtime_dir = if cfg!(debug_assertions) {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../runtime")
        } else { app.path().resource_dir()?.join("runtime") };
        app.manage(std::sync::Arc::new(Bridge { worker: Mutex::new(None), data_dir, runtime_dir }));
        Ok(())
    }).invoke_handler(tauri::generate_handler![rpc]).build(tauri::generate_context!())
        .expect("RainForge desktop initialization failed").run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                let bridge = app.state::<std::sync::Arc<Bridge>>();
                if let Ok(mut worker) = bridge.worker.lock() { *worker = None; }
            }
        });
}
