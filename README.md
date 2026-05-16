[English readme](#uv-island-pixel-snap-1) | [Details (Claude summarized)](#details-claude-summarized)
# UV Island Pixel Snap
一個用於將 UV island 的邊界框中心移動至所設定解析度中最接近像素位置的 Blender 附加元件，此為透過向 Claude 反覆提示、測試而成。

僅在 Blender 4.5 LTS 和 5.1 中測試，處理速度依所選取的 UV island 數量而異。

<img width="256" height="332" alt="screen" src="https://github.com/user-attachments/assets/f1b98da2-b8fa-48ea-80bc-b3008c4e94f6" />

## 安裝
自 [Releases](https://github.com/nokau/uv-island-pixel-snap/releases) 下載`.zip`檔案（不是名為「Source Code」的其他檔案）

啟動 Blender：

- 將下載的`.zip`檔案拖曳放進 Blender 視窗內，選擇安裝位置，點擊`OK`

  _或_

- 前往`編輯 > 偏好設定`，選擇`附加元件`，找到該視窗右上角的下拉選單按鈕，選擇`從磁碟安裝...`，找到下載的`.zip`文件，選擇安裝位置，點擊`從磁碟安裝`

## 使用方法
- 附加元件啟用後，UV 編輯器側邊欄（預設快速鍵為「N」）上會出現一個「UV Island Pixel Snap」面板

- 在當中設定影像大小、調整吸附模式選項，並選擇 UV island

- 點擊當中`兩者`、`X`、`Y`其中一按鈕執行

> [!NOTE]
> 當 UV island 於最對齊狀態時，畫面底部的狀態列中將短暫提示：<img width="362" height="24" alt="screen2" src="https://github.com/user-attachments/assets/53b89495-42db-46b2-9daf-69624a51916e" />

> [!NOTE]
> 啟用`UV 同步選取`後，此附加元件的側邊欄面板底部會提示將`選取模式`切換為`面`。

> [!NOTE]
> 有時要執行多次才會移動 UV island 至最對齊狀態。

# UV Island Pixel Snap
A Blender extension to snap UV island bounding box centers to the nearest pixel of a specific image resolution, built through iterative prompting and testing with Claude.

This is only tested in Blender 4.5 LTS and 5.1, performance may vary depending on the amount of selected islands.

<img width="256" height="332" alt="screen" src="https://github.com/user-attachments/assets/f1b98da2-b8fa-48ea-80bc-b3008c4e94f6" />

## Installation
Download the `.zip` file from the latest [Releases](https://github.com/nokau/uv-island-pixel-snap/releases) (not the ones named Source Code).

Start Blender:

- Drag the file and drop it within blender's window, select where it'll be installed and click `OK`

  _OR_
  
- Go to _Edit > Preferences_, select the _Add-ons_ tab, locate the drop down button on the top right, select `Install from disk...` and find the downloaded `.zip` file, select where it'll be installed and click `Install from disk`

## How to use
- Once the extension is enabled, a `UV Island Pixel Snap` panel can be found on UV Editor's sidebar (hotkey N by default)

- Set _Image Size_ and configure _Snap Mode_, then select desired islands

- Click one of the `Both`,`X`,`Y` buttons to start the operation

> [!NOTE]
> When UV islands are at their most aligned state, its hinted temporarily in the status bar at the bottom of the screen: <img width="362" height="24" alt="screen2" src="https://github.com/user-attachments/assets/53b89495-42db-46b2-9daf-69624a51916e" />

> [!NOTE]
> When `UV Sync Selection` is ON, the extension's sidebar panel will note to switch Select Mode to Face.

> [!NOTE]
> Sometimes it needs multiple snaps for the island to be at their most aligned state.

# Details (Claude summarized)
<img width="640" height="320" alt="uv_snap_diagram_bg" src="https://github.com/user-attachments/assets/a3442b14-a935-46d7-b2f1-0d1cacb0ee7e" />

## Main Files
`uv_island_pixel_snap_b4.py` and `uv_island_pixel_snap_b5.py` each containing a version of operation for Blender 4 and 5.

`__init__.py` checks Blender version and which to use.

## What's the same in both
The entire snap pipeline is identical — the 5-pass structure, bounding box math, pixel corner/center formula, axis mode, merge overlapping with convex hull + SAT, the operator/panel/properties classes, and all the feedback messages.

## Where they split
#### Selection reading (`face_has_selection`)
B4 uses `loop[uv_layer].select` — a flag that lives directly on the UV loop data, granular per corner, works across all UV select modes. `face.select` is always checked first as a staleness gate since 3D viewport clicks clear it reliably even when UV flags linger.

B5 removed `loop[uv_layer].select` entirely. The replacement is `loop.uv_select_vert` — a proper attribute on the loop itself, added in Blender 5.0 per the release notes. With sync off it correctly distinguishes islands where `face.select` spreads mesh-wide. With sync on, `face.select` is used directly since 'A' key and mesh-level selections update it reliably but don't always update `uv_select_vert` immediately.

### Selection writing (`apply_uv_selection`)
B4 writes `loop[uv_layer].select` and `loop[uv_layer].select_edge` directly, keeping everything contained in the UV editor without bleeding into the 3D viewport face selection.

B5 writes via `loop.uv_select_vert_set()` when sync is off — the proper B5 write method. When sync is on, it falls back to `face.select` since that's what Blender uses as the authoritative signal in sync mode.

### Pass 5 flush
B4 just calls `bmesh.update_edit_mesh(me)` — nothing else. `select_flush` spreads selection incorrectly so it's skipped entirely, and none of the B5 UV sync methods exist in B4.

B5 calls `bm.uv_select_sync_from_mesh()` before `update_edit_mesh` when sync is on — this pushes `face.select` into the UV editor's selection state. With sync off it's skipped because `uv_select_vert_set` handles everything directly and `uv_select_sync_from_mesh` would spread selection mesh-wide.

### Panel warnings
Both files now share the same logic — silent when sync is off (both versions work reliably across all UV select modes in that state), and shows the UV Sync ON indicator with a Face mode warning when sync is on.
