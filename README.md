# 梅露可物語 離線版 故事翻譯器

《メルクストーリア - 癒術士と心の旋律 -》（Steam 離線版）的故事閱讀與翻譯工具：從你自己安裝的遊戲讀出故事劇本，翻成繁體中文（台灣），並以**與遊戲原版相同的介面**播放（背景、立繪表情、BGM、音效、影片、Timeline 演出）。

本專案已附上全部 3,958 話的中文譯文（`work/zh/`）與名詞對照表（`work/glossary.json`），裝好就能直接看中文故事；也可以用自己的翻譯引擎重新翻譯或修改譯文。

## 需要準備

- **Steam 版遊戲**已安裝在這台電腦（或可存取的磁碟上）。本工具只讀取遊戲檔，不修改。
- **Python 3.10 以上**。第一次啟動會自動在 `~/.venvs/merc-story` 建立執行環境並安裝套件（UnityPy、Pillow、soundfile 等，約需幾分鐘與網路連線）。
- 網頁瀏覽器（Chrome、Edge、Safari、Firefox）。

## 使用方式

1. 啟動：macOS 雙擊 **`啟動翻譯器.command`**；Windows 雙擊 **`啟動翻譯器.bat`**。瀏覽器會開啟 <http://localhost:8765>。
2. 程式會自動尋找 Steam 的遊戲資料夾。找不到時，到「設定」分頁填入遊戲安裝目錄（例如 `C:\Program Files (x86)\Steam\steamapps\common\メルクストーリア - 癒術士と心の旋律 -`）後按「儲存設定」。
3. 按右上「開啟播放器」，或直接瀏覽 <http://localhost:8765/output/index.html>，選章節與話數就能播放。

使用期間請保持啟動視窗開著；關掉視窗就會停止伺服器。

### 不佔硬碟的播放方式

播放器用到的素材**不會解出到硬碟**：

- **點選某一話時**，伺服器會在背景把這一話用到的背景、立繪、BGM、音效先解碼到**記憶體**，播放時直接取用；影片在播放到時才解開。
- 記憶體快取預設上限 512 MB，超過時自動丟掉最久沒用的素材（可在 `work/settings.json` 調整 `media_cache_mb`）。
- 每一話的劇本資料（`data/ja/`、`output/data/`）在**第一次開啟該話時**才從遊戲檔建立，一話約幾十到幾百 KB；只有看過或翻譯過的話才會產生。
- Timeline 演出、SpriteStudio 文字特效與播放器介面圖片已隨專案附上（`output/assets/timeline|ss|ui`，約 35 MB）。

因此播放時需要啟動翻譯器、而且能讀到遊戲檔。若想把播放器整包帶到沒有遊戲的地方離線看，可以另外用 `python tools/build_player.py --files <話數…>` 把素材匯出成檔案（會佔用較多空間）。

### 翻譯與校對

1. 「設定」分頁選擇**翻譯引擎**（見下節），設定網址／金鑰與模型後按「儲存設定」。右上角綠色代表可以翻譯。
2. 左側勾選章節或單話，上方會顯示句數、字數與費用估計。
3. **① 建立名詞表** → 到「名詞對照表」分頁檢查／修改譯名（改過的會標為「使用者指定」並鎖定）。
4. **② 開始翻譯**。已有譯文的話會略過，勾選「重新翻譯」才會覆蓋。
5. 「譯文校對」分頁逐句對照修改（有警告的句子標紅，「▶播放」可跳到該句）。每則警告旁有「接受」「拒絕」：
   - **接受**：依警告修改該句並立即儲存。括號、行數、禁用字用規則修正；名詞、外文、標籤、漏譯交給目前的翻譯引擎做最小幅度修改。
   - **拒絕**：刪除警告，譯文不變；之後重新驗證也不會再出現。
6. 修改後不需要另外匯出：播放器開啟該話時會自動用最新的譯文與名詞表更新。

## 翻譯引擎

| 引擎 | 需要設定 | 結構化輸出方式 | 備註 |
|---|---|---|---|
| Anthropic Claude | API 金鑰、模型（Opus 5.5 / Sonnet 5.5 / Haiku 5.5）、effort | `output_config.format` JSON Schema | 名詞表放進 prompt cache；拒答時自動改用備援模型；顯示費用估計 |
| 遠端 Ollama | 服務網址（如 `http://192.168.1.20:11434`）、模型、num_ctx；金鑰選填（放在 Bearer 驗證的反向代理後面時） | `/api/chat` 的 `format` 傳 JSON Schema（Ollama 0.5 以上） | 「取得模型清單」會呼叫 `/api/tags`，顯示參數量與量化格式 |
| 遠端 vLLM | 服務網址（如 `http://192.168.1.20:8000`，結尾有沒有 `/v1` 都可以）、模型；金鑰選填（vLLM 有加 `--api-key` 時） | 依序嘗試 `response_format: json_schema` → `guided_json` → `json_object`，記住伺服器支援的方式 | 「取得模型清單」呼叫 `/v1/models`，顯示最大上下文 |

- 模型可從下拉清單選，也可手動輸入名稱。「取得模型清單／測試連線」用的是表單上尚未儲存的網址與金鑰，可以先測再存。
- 自架模型的上下文通常較小，所以每批預設 30 句（Claude 60 句），而且名詞表只送本批出現的條目；遇到「輸出被截斷」時請調低「每批句數」或加大 num_ctx（Ollama 預設 2048／4096，建議至少 16384）。
- 推理型模型輸出的 `<think>…</think>` 與 ```json 圍欄會自動去除。
- Ollama「思考模式」預設**關閉**：實測 qwen3.8:27b 一批 30 句，關閉約 7 秒、開啟約 18 秒，譯文品質相近。
- 自架模型偶爾混入簡體字（如「什么」），會自動轉成繁體（只轉換純簡體字，「面」「里」等繁簡共用字不動）；原文整句有「」『』而譯文漏掉外層括號時會自動補回，句首句尾括號不一致則列為警告。

- 自架服務不計 API 費用，進度與估價改顯示 token 數。所有金鑰都只存在 `~/.config/merc-story-translator/credentials.json`（權限 600），不會寫進專案資料夾或輸出檔。
- 每話譯文會記錄使用的引擎與模型（例如 `ollama:qwen3:32b`），可在「譯文校對」分頁看到；名詞表條目的「來源」也會標出是哪個模型建議的。

## 翻譯流程

| 步驟 | 本工具對應 |
|---|---|
| 0.3 參考資料（`--ref`） | 「設定 → 參考資料」：網址或文字，整理成 `work/refs.md` 名詞筆記（≤200 行） |
| 0.3.3 指定譯名（`--term`，最高優先） | 名詞表中「使用者指定／鎖定」的條目 |
| 3.6 建立專有名詞對照表 | ① 建立名詞表：擷取說話者、片假名詞、『』內詞、地名組織名 → Claude 決定唯一譯名 → `work/glossary.json`（全作共用，跨章節固定） |
| 3.6.3 顯示給使用者確認 | 「名詞對照表」分頁可搜尋、修改、鎖定、刪除 |
| 4 分批 | 每話依序每 60 句一批，附前 8 句的原文＋譯文作脈絡 |
| 5 對照表規範 | 名詞表放在 system prompt（有 prompt cache）；翻譯中出現的新專有名詞即時補進名詞表，後續批次沿用 |
| 5 台灣用語、避免「妳」 | 規則寫在提示詞，且輸出後自動把「妳」改成「你」 |
| 5.5 驗證 | 禁用字、括號、`<name>`／`[RRGGBB]`／`[-]` 標籤保留、外文（名詞表規定的英文譯名不算）、行數、名詞表一致性 → 每句警告存在譯文檔，校對分頁可「接受」或「拒絕」 |
| 6.1 完整性檢驗 | 每批檢查每個句號（場景編號）都有譯文；缺漏自動重譯一次，仍缺則列為「漏譯」警告 |
| 6.3 保存對照表 | `work/glossary.json` 永久保存，下次翻譯其他章節自動沿用 |

> 費用參考：第二部一章（20 話、約 3,800 句、9.4 萬日文字）用 Opus 5.5 估計約 US$5～6；整個第二部（427 話）約 US$110～130。Sonnet 5.5 約一半。估價只是依字數推算。

## 播放器操作（與原版相同）

- 點畫面／Enter／空白鍵／→：下一句（打字中則顯示全文）；**BACK**（←）回上一句
- 右上 **MENU**：スキップ（跳到本話結尾）、ログ（對話紀錄，滑鼠滾輪往上也可開啟）、オート（自動播放：每按一次輪流切換 ▶ → ▶▶ → ▶▶▶ → 關閉，箭頭越多越快；鍵盤 A 鍵相同）、UI表示（隱藏介面）、**中／雙／日**（中文、雙語、日文原文切換）
- 目錄頁「設定」：主角名字（取代 `<name>`，預設「優」）、BGM／音效音量、文字速度
- 影片播放中點畫面會暫停並詢問是否略過（原版訊息「この動画をスキップしますか？」），選「取消」會繼續播放；影片播完才切換該場景的 BGM
- 網址加 `&at=場景編號` 可從指定場景開始

## 資料夾結構

```
啟動翻譯器.command / .bat  啟動器（macOS / Windows）
translator/            翻譯器：server.py（本機網頁伺服器）、media.py（素材即時解碼與記憶體快取）、
                       pipeline.py（翻譯流程）、backends.py（Claude／Ollama／vLLM）、web/（介面）
tools/                 遊戲資料解析：merc_format.py（解密＋MemoryPack）、addressables_catalog.py、game_data.py、
                       build_player.py（產生播放器劇本資料）、timeline.py、spritestudio.py、player_src/（播放器前端）
work/zh/<storyId>.json 中文譯文（隨專案附上）    work/glossary.json 名詞對照表（隨專案附上）
work/chapters_zh.json  活動故事等章節名稱的中文（翻譯時自動補上未翻譯的章節，也可手動修改；沒有譯名的章節顯示日文）
work/settings.template.json  設定樣板：第一次啟動時複製成 work/settings.json（含預設的追加翻譯規則：
                       梅露可的口癖「みゅ」→「呀」、圖圖的叫聲「きゅ」等→「啾」）
work/settings.json     你的設定（不納入版本控制；API 金鑰另存在 ~/.config/merc-story-translator/）
data/                  從遊戲檔建立的日文劇本與章節資料（第一次用到時產生，不納入版本控制）
output/                播放器：index.html、player.html、player/；assets/timeline|ss|ui 隨專案附上；
                       data/ 為第一次開啟時產生的劇本資料
```

## 技術說明（遊戲資料格式）

以 REA 的 `inspect_il2cpp_metadata`（global-metadata.dat v39，連結 GameAssembly.dll 取得方法位址）找出故事系統的類別，再對關鍵函式反組譯：

- **劇本**：`StoryMasterData/*.bundle` 內的 TextAsset `StoryYamlData_<id>`，由 `CoreLibrary.Adapter.DataFile.DefaultEncryptor` 加密：
  AES-256-CBC／PKCS7，金鑰 = PBKDF2-HMAC-SHA256(密碼 `"2147483647"`（int.MaxValue）, 鹽 `"-2147483648"`（int.MinValue）, 1024 次, 32 bytes)，IV 為密文前 16 bytes。
- 解密後是 **MemoryPack** 序列化的 `StoryYamlData { int Id; Dictionary<int, StorySceneYamlData> }`；每個場景 21 個欄位：說話者、台詞、訊息框類型（Default/Shout）、字級（Default/Middle/Big）、背景＋時段、背景特效、BGM、音效、畫面效果（Fade/Shake/Earthquake/Still/Wipe/CrossFade/Heal/Summon/Scroll/Movie…）、左中右三個角色（立繪 ID、臉 ID、表情、感情圖示、出場動畫、是否發言中）、SpriteStudio 特效。欄位順序與型別取自 metadata 中各類別的 backing field。
- **名字牌**（`StorySceneData.SetSpeakers` 反組譯）：場景的 `speakers` 是角色的 key，依序比對左、中、右角色，key 相同就顯示該角色的 `displayName`（例如 key「過去メルク」顯示為「カラコリチア」，`<name>` 換成主角名），多人以「・」連接；畫面上沒有對應角色的說話者不顯示名字。同一個 key 在不同場景可能顯示不同名字，所以逐場景判斷。名詞表、翻譯時的說話者資訊與播放器都用 displayName。
- **分段劇本**：`StoryMasterData` 的 `children` 欄位列出同一話的後續段落（例如 2111 →「2111_3」、4347 →「4347_1」「4347_3」「4347_4」），原版在本體播完後依序接著播；缺號的段落是戰鬥，沒有劇本。共 5 話有分段（2111、3288、3624、4283、4347），解出時會把段落接成一份劇本。已翻譯過的話若因此多出句子，翻譯時只會翻新增的部分，原有譯文保留。
- **章節**：`MasterData/ChapterMasterData`、`StoryMasterData`（同樣加密＋MemoryPack）。第二部 = 26 章 427 話。
- **素材定位**：Addressables 2.x 二進位 catalog（`catalog_*.bin`）；自行實作解析器（字串為 `{stringId,nextId}` 反向鏈結串列）。背景 `Story/StoryBackground/<id>_<時段>.png`、立繪 `Story/StoryActress/<id>.png`、音訊 `Sound/B-*.mp3`／`S-*.mp3`（FMOD Vorbis → 轉 MP3）、影片 `Common/Movie/*.mp4`（VideoClip，`m_ExternalResources` 指向 `.resource` 內的原始 MP4）。
- **立繪圖集**（`CharacterActressConstant`／`DefaultActress`／`NaviActress` 的 .cctor 反組譯）：1024×1024 圖集，Default 身體 (2,2,700,788)、臉 150×150、格距 153，表情依 喜/喜d/怒/怒d/哀/哀d/驚/驚d/呆/照/眠/真剣 兩欄排列、固有1～4 在底列；Navi（梅露可、托托等）身體 (2,2,400,500)、臉 200×200、格距 203。臉的貼合位置來自 `ActressPositionYamlData`。非發言角色亮度 50%（`CharacterActressView.InactiveColor` = 128/255）。**Scout**（可招募的夥伴角色，多見於メモリアルストーリー等）不用 StoryActress 圖集，而是 `Unit/LargeNoStage/<unitId>.png`（無背景全身圖 800×900），整張畫在 700×788 的身體框（`ScoutActress..cctor` 的 `Vector2(700, 788)`），匯出為 `assets/chara/u<unitId>.webp`。
- **Timeline 演出**：場景的 `assetParams` 有 `assetType == "Timeline"` 時，遊戲生成 `BundleAssets/Particle/StoryTimeline_<名稱>/StoryTimeline_<名稱>.prefab`，由 PlayableDirector（`StoryTimelineComponent`）播放，不是影片檔。目前有 6 個：`main2_sizuku`（4347）、`main2_sizuku_kaerimichi`＋`main2_Endroll` 片尾名單（4348）、`main2_EndrollStory`（4349）、`main2_sora`（4301）、`decemberspecial_2`（4283）。bundle 有 typetree，可直接讀出物件階層、SpriteRenderer、MeshRenderer＋材質、粒子、Animator、Timeline 軌道（動畫／啟用／群組），以及遊戲自訂的標記：`[UI] テキストフェードイン／アウト`（畫面下方的台詞，要翻譯）、`[UI] イベント指定`（`TimelineUIEvent`：1＝WaitText、2＝WaitTap 等點擊、3＝NextScene 進下一場景）、`[Bgm] イベント指定`（`TimelineBgmEvent`：1＝FadeIn、2＝FadeOut）、`SE Marker`；`TimelineAnimationClipTrack` 是 Animancer 播放的片段。AnimationClip 是 muscle clip：streamed 曲線每個 key 存 (曲線索引, a, b, c, d)，區段值 =((a·dt+b)·dt+c)·dt+d；綁定路徑與屬性是 CRC32，材質屬性（customType 22）為 `(CRC32(名稱) & 0x0FFFFFFF) | (分量 << 28)`。座標為正交攝影機、可見 568×320 單位、z 小者在前。`[UI] Animator操作`（`TimelineAnimatorEvent`：1＝SetTrigger、2＝隨機）透過 `TimelineAnimatorReceiver.animator` 操作 Animator 狀態機（Trigger、Exit Time、Any State）。部分演出用 RenderTexture 攝影機分鏡：攝影機只拍 culling mask 內的圖層（17～20），畫面貼在 `renderTextureMeshRenderer` 的平面上，以材質 `_Alpha` 交叉淡化（sora 還會再經過一台 bloom 攝影機）。WaitText 時演出停在畫面上，故事接著用訊息框顯示台詞，下一個 `waitTarget` 場景再接著播（4283 的 `decemberspecial_2` 跨 15 個場景、分兩段 Timeline）。有些台詞是畫成圖片的（`txt_001`…），原文抄錄在 `tools/timeline_image_texts.json`，翻譯後由播放器以 Canvas 畫成同樣風格的貼圖取代（雙語模式下，原本的日文圖片縮小接在中文下方）。`tools/timeline.py` 匯出成 `assets/timeline/<名稱>/tl.js`（圖片以 data URI 內嵌，直接用 file:// 開啟也能用 WebGL），`player/timeline.js` 用 WebGL 重現；台詞在翻譯時以索引 10000×(場景+1)+序號 接在該場景之後。
- **SpriteStudio 文字特效**：`assetType == "EffectSpriteStudio"` 的特效由 SS6 for Unity 播放：`SpriteStudio/Effect/<名稱>/` 的 `DataAnimation`（da_）與 `DataCellMap`（dc_）是有 typetree 的 MonoBehaviour。屬性以 PackAttribute StandardCPE 儲存：只記數值改變的格，`TableCode` 低 15 位元是格數、高位元是 `TableValue` 索引；`Status.Flags` 0x20000000＝隱藏，`StatusParts` 0x10000000＝此動畫沒用到、0x08000000＝不受遮罩影響。遮罩零件（Feature 6／7）的不透明度是門檻：遮罩貼圖 alpha 大於門檻的地方會遮住受遮罩影響的零件（不透明度 0＝整塊遮住，用來做文字由左到右逐行出現；漸層遮罩配合不透明度變化做出擦入效果）。每個 CellMap 在 `TableMaterial` 佔 20 個材質，第一個材質的 `_MainTex` 就是它的貼圖；零件的 `OperationBlendTarget` 1＝加算；`PartsColor`（零件顏色，混合／加算／減算／乘算）與 `ScalingLocal` 也會重現；網格零件（Feature 13，骨架彎曲）以未變形的四邊形近似，頂點變形不重現。目前重現的文字特效（活動故事裡把台詞畫在圖上的全部）：`Storyparts_lspr`（2847、2856）、`Storyparts_fairy_1`（1934、1938、1942 的書信）、`Storyparts_snowtext`（1461、1475）、`Storyparts_vote1_oukoku_1`（3061～3075 的活動標題、副標、一回戦、続）、`Storyparts_decemberspecial_3`（4285）、`Storyparts_wa_3_1` 的動畫 2（2138 的毛筆字「払暁蜘蛛」）。原文抄錄在 `tools/spritestudio_texts.json`（只有列在這裡的特效會被重現，其他仍用 CSS 近似；`_anims` 可以只重現特定動畫）。`Storyparts_syousu_4_2` 的 effect_moji 是虛構的符文，不是日文，保留原圖。另外以原圖重現（沒有文字）的特效：`Storyparts_general_1`（黑／白集中線，約 400 話）、`Storyparts_pappakapan_ss`（「ぱっぱかぱん」放射光，57 話）。畫面效果 `Heal`（癒術）依原版 `StoryHealEffectView` 重現：全畫面 80% 黑底，畫面中央以 0.5 倍播放 `effect_iyashi` 的動畫「1」，速度 0.7 倍。特效的前後順序依 `assetParams.position.z`：大於 0 畫在背景與角色之間（例如放射光在角色後面），其餘畫在角色前面（集中線）。`spritestudio_texts.json` 或 `timeline_image_texts.json` 修改後，劇本快取與播放資料會在下次開啟時自動重建。`tools/spritestudio.py` 把動畫烘焙成逐格繪製清單，輸出 `assets/ss/<名稱>/ss.js`＋`tex0.png`…，`player/spritestudio.js` 用 Canvas 播放；中文模式下用譯文重畫文字（每一行對齊原圖該行的位置並縮在原本的寬高內，遮罩出現的時機不變，雙語模式也顯示譯文），顯示的台詞會記到對話紀錄。台詞翻譯索引接在 Timeline 台詞之後（10000×(場景+1)+序號），譯文的行數要和原圖一致。
- **介面版面**：`StoryRenderTextureWorkspace.prefab` 的 RectTransform（1136×640）：訊息框 1136×218 貼底、文字區 700×140（字級 24、顏色 #46382F）、NEXT/BACK 在 ±430、名字牌 300×40、右上 MENU 72×72 與子按鈕；UI 圖片取自 `StoryUI.spriteatlasv2`。

## 已知限制

- Scout 角色的表情：全部資料中 47,031 次出場只有 4 次不是「通常」，目前一律顯示全身圖本來的表情。
- SpriteStudio 動畫特效（霧、雨、雪、火焰等約 5% 場景）以 CSS 近似（`spritestudio_texts.json` 列出的文字特效除外）；角色感情圖示以符號近似。
- Timeline 演出是重現而非錄影：自訂著色器以近似處理（ArtisticFade 溶解、Overlay、飽和度、RenderTexture 分鏡的透明度有做；放射模糊、色差、高斯模糊等後製效果略過），粒子為簡化模擬。播放中點畫面只會在出現「下一頁」圖示時繼續；略過演出用右上角的 SKIP。
- 字型使用 Google Fonts（粉圓 Huninn／RocknRoll One）；離線時改用系統字型。
- Android XAPK 只含程式與內嵌 UI，故事 bundle 是遊戲執行時下載的，因此以 Steam 版資料為準。
- 劇本的 `Movie` 效果（第 2260、2556、3624、4344、4347 話）播放時才從遊戲檔解開影片：解開期間會暫時多用約 2 GB 記憶體（在子程序裡，結束後歸還），伺服器只保留目前這一部（最大 402 MB）。
