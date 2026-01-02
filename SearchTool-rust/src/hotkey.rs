// 全局热键模块（Windows专用）
#[cfg(windows)]
pub mod windows_hotkey {
    use std::sync::mpsc::{channel, Sender, Receiver};
    use std::thread;

    pub enum HotkeyEvent {
        ShowMain,
    }

    pub struct HotkeyManager {
        tx: Option<Sender<HotkeyEvent>>,
        rx: Option<Receiver<HotkeyEvent>>,
        thread_handle: Option<thread::JoinHandle<()>>,
    }

    impl HotkeyManager {
        pub fn new() -> Self {
            Self {
                tx: None,
                rx: None,
                thread_handle: None,
            }
        }

        pub fn start(&mut self) -> Result<(), Box<dyn std::error::Error>> {
            use windows::Win32::UI::Input::KeyboardAndMouse::*;
            use windows::Win32::UI::WindowsAndMessaging::*;

            let (tx, rx) = channel();
            self.tx = Some(tx.clone());
            self.rx = Some(rx);

            self.thread_handle = Some(thread::spawn(move || {
                unsafe {
                    // Ctrl+Shift+Alt+Space（呼出主窗口）
                    RegisterHotKey(
                        None,
                        1,
                        HOT_KEY_MODIFIERS(MOD_CONTROL.0 | MOD_SHIFT.0 | MOD_ALT.0),
                        VK_SPACE.0 as u32,
                    );

                    let mut msg: MSG = std::mem::zeroed();
                    while GetMessageW(&mut msg, None, 0, 0).as_bool() {
                        if msg.message == WM_HOTKEY {
                            match msg.wParam.0 {
                                1 => {
                                    let _ = tx.send(HotkeyEvent::ShowMain);
                                }
                                _ => {}
                            }
                        }
                    }

                    UnregisterHotKey(None, 1);
                }
            }));

            Ok(())
        }

        pub fn get_events(&self) -> Vec<HotkeyEvent> {
            let mut events = Vec::new();
            if let Some(rx) = &self.rx {
                while let Ok(event) = rx.try_recv() {
                    events.push(event);
                }
            }
            events
        }
    }
}

#[cfg(not(windows))]
pub mod windows_hotkey {
    pub enum HotkeyEvent {
        ShowMain,
    }

    pub struct HotkeyManager;

    impl HotkeyManager {
        pub fn new() -> Self {
            Self
        }

        pub fn start(&mut self) -> Result<(), Box<dyn std::error::Error>> {
            log::warn!("全局热键仅支持Windows系统");
            Ok(())
        }

        pub fn get_events(&self) -> Vec<HotkeyEvent> {
            Vec::new()
        }
    }
}

