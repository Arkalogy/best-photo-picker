//! The application menu bar (macOS needs it for keyboard accelerators)
//! and what each item does in the page.

use tauri::{
    menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu},
    Manager,
};
use tauri_plugin_shell::ShellExt;

use crate::sidecar::restart_server;

/// Build the menu bar and route its events into the page.
pub(crate) fn install_menu(app: &tauri::App) -> tauri::Result<()> {
    let handle = app.handle().clone();
    // Build app menu bar (macOS needs this for keyboard accelerators)
    let preferences_item = MenuItem::with_id(
        app, "preferences", "Settings\u{2026}", true, Some("CmdOrCtrl+,"),
    )?;
    let app_submenu = Submenu::with_items(
        app,
        "Best Photo Picker",
        true,
        &[
            &PredefinedMenuItem::about(
                app,
                Some("About Best Photo Picker"),
                Some(AboutMetadata {
                    name: Some("Best Photo Picker".into()),
                    version: Some(env!("CARGO_PKG_VERSION").into()),
                    copyright: Some("© 2026 Arkalogy LLC".into()),
                    credits: Some("Developed with AI-assisted coding".into()),
                    icon: tauri::image::Image::from_path("icons/icon.png").ok(),
                    ..Default::default()
                }),
            )?,
            &PredefinedMenuItem::separator(app)?,
            &preferences_item,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, Some("Hide Best Photo Picker"))?,
            &PredefinedMenuItem::hide_others(app, None)?,
            &PredefinedMenuItem::show_all(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, Some("Quit Best Photo Picker"))?,
        ],
    )?;
    let open_library_item = MenuItem::with_id(
        app, "open_library", "Open Library\u{2026}", true, Some("CmdOrCtrl+O"),
    )?;
    let new_library_item = MenuItem::with_id(
        app, "new_library", "New Library\u{2026}", true, Some("CmdOrCtrl+Shift+N"),
    )?;
    let import_item = MenuItem::with_id(
        app, "import", "Import Photos\u{2026}", true, Some("CmdOrCtrl+I"),
    )?;
    let export_item = MenuItem::with_id(
        app, "export", "Export\u{2026}", true, Some("CmdOrCtrl+E"),
    )?;
    let restart_server_item = MenuItem::with_id(
        app, "restart_server", "Restart Server", true, None::<&str>,
    )?;
    let file_submenu = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &open_library_item,
            &new_library_item,
            &PredefinedMenuItem::separator(app)?,
            &import_item,
            &export_item,
            &PredefinedMenuItem::separator(app)?,
            &restart_server_item,
        ],
    )?;
    let search_item = MenuItem::with_id(
        app, "search", "Search", true, Some("CmdOrCtrl+K"),
    )?;
    let edit_submenu = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &search_item,
        ],
    )?;
    // View menu
    let toggle_sidebar_item = MenuItem::with_id(
        app, "toggle_sidebar", "Toggle Sidebar", true, Some("CmdOrCtrl+\\"),
    )?;
    let zoom_in_item = MenuItem::with_id(
        app, "zoom_in", "Zoom In", true, Some("CmdOrCtrl+="),
    )?;
    let zoom_out_item = MenuItem::with_id(
        app, "zoom_out", "Zoom Out", true, Some("CmdOrCtrl+-"),
    )?;
    let slideshow_item = MenuItem::with_id(
        app, "slideshow", "Slideshow", true, None::<&str>,
    )?;
    let fullscreen_item = PredefinedMenuItem::fullscreen(app, None)?;
    let activity_log_item = MenuItem::with_id(
        app, "activity_log", "Activity Log", true, None::<&str>,
    )?;
    let view_submenu = Submenu::with_items(
        app,
        "View",
        true,
        &[
            &toggle_sidebar_item,
            &PredefinedMenuItem::separator(app)?,
            &zoom_in_item,
            &zoom_out_item,
            &PredefinedMenuItem::separator(app)?,
            &slideshow_item,
            &PredefinedMenuItem::separator(app)?,
            &activity_log_item,
            &PredefinedMenuItem::separator(app)?,
            &fullscreen_item,
        ],
    )?;
    // Window menu
    let window_submenu = Submenu::with_items(
        app,
        "Window",
        true,
        &[
            &PredefinedMenuItem::minimize(app, None)?,
            &PredefinedMenuItem::maximize(app, Some("Zoom"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, Some("Close Window"))?,
        ],
    )?;
    // Help menu
    let help_docs_item = MenuItem::with_id(
        app, "help_docs", "Documentation", true, None::<&str>,
    )?;
    let help_issues_item = MenuItem::with_id(
        app, "help_issues", "Report an Issue\u{2026}", true, None::<&str>,
    )?;
    let help_repo_item = MenuItem::with_id(
        app, "help_repo", "GitHub Repository", true, None::<&str>,
    )?;
    let help_updates_item = MenuItem::with_id(
        app, "help_updates", "Check for Updates\u{2026}", true, None::<&str>,
    )?;
    let help_submenu = Submenu::with_items(
        app,
        "Help",
        true,
        &[
            &help_updates_item,
            &PredefinedMenuItem::separator(app)?,
            &help_docs_item,
            &help_repo_item,
            &PredefinedMenuItem::separator(app)?,
            &help_issues_item,
        ],
    )?;
    let app_menu = Menu::with_items(
        app,
        &[&app_submenu, &file_submenu, &edit_submenu, &view_submenu, &window_submenu, &help_submenu],
    )?;
    app.set_menu(app_menu)?;

    // Handle app menu events
    let handle_menu = handle.clone();
    app.on_menu_event(move |app_handle, event| {
        if let Some(window) = handle_menu.get_webview_window("main") {
            match event.id().as_ref() {
                "search" => {
                    let _ = window.eval(
                        "if(typeof isSearchOpen==='function'){\
                         if(isSearchOpen())hideSearch();else showSearch();}"
                    );
                }
                "preferences" => {
                    let _ = window.eval(
                        "if(typeof showSettings==='function')showSettings();"
                    );
                }
                "open_library" => {
                    let _ = window.eval(
                        "if(typeof showLibraryPicker==='function')showLibraryPicker();"
                    );
                }
                "new_library" => {
                    let _ = window.eval(
                        "if(typeof createNewLibrary==='function')createNewLibrary();"
                    );
                }
                "import" => {
                    let _ = window.eval(
                        "if(typeof showImportModal==='function')showImportModal();"
                    );
                }
                "export" => {
                    let _ = window.eval(
                        "if(typeof showExportModal==='function')showExportModal();"
                    );
                }
                "restart_server" => {
                    restart_server(app_handle);
                }
                // View menu
                "toggle_sidebar" => {
                    let _ = window.eval(
                        "if(typeof toggleSidebar==='function')toggleSidebar();"
                    );
                }
                "zoom_in" => {
                    let _ = window.eval(
                        "if(typeof applyZoom==='function'){let z=parseInt(document.getElementById('zoom-slider')?.value||80)+10;applyZoom(Math.min(z,200),true);}"
                    );
                }
                "zoom_out" => {
                    let _ = window.eval(
                        "if(typeof applyZoom==='function'){let z=parseInt(document.getElementById('zoom-slider')?.value||80)-10;applyZoom(Math.max(z,40),true);}"
                    );
                }
                "slideshow" => {
                    let _ = window.eval(
                        "if(typeof startSlideshow==='function')startSlideshow(0);"
                    );
                }
                "activity_log" => {
                    let _ = window.eval(
                        "if(typeof showActivityLog==='function')showActivityLog();"
                    );
                }
                // Help menu
                "help_updates" => {
                    let _ = window.eval(
                        "if(typeof checkForUpdates==='function')checkForUpdates(true);"
                    );
                }
                "help_docs" => {
                    let _ = app_handle.shell().open(
                        "https://github.com/Arkalogy/best-photo-picker#readme",
                        None,
                    );
                }
                "help_repo" => {
                    let _ = app_handle.shell().open(
                        "https://github.com/Arkalogy/best-photo-picker",
                        None,
                    );
                }
                "help_issues" => {
                    let _ = app_handle.shell().open(
                        "https://github.com/Arkalogy/best-photo-picker/issues/new",
                        None,
                    );
                }
                _ => {}
            }
        }
    });
    Ok(())
}
