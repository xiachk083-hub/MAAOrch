# MAA/maa-cli 涓嬭浇鏇存柊涓庝唬锟?

## 鏇存柊妫€锟?

`UpdateCheckThread`锛坄services/update_service.py`锛夐€氳繃 GitHub Release API 鏌ヨ鏈€鏂扮増鏈細

```
GET https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/releases/latest
Headers: User-Agent: MAA-Launcher
```

杩斿洖鏁版嵁瑙ｆ瀽锟?

- `tag_name` 锟?鐗堟湰鍙凤紙锟?`v6.11.1`锟?
- `assets[]` 锟?鎸夊钩鍙拌繃锟?`win-x64` / `win-arm64` 锟?`.zip` 锟?
- 鎺掗櫎 debug symbol 锟?component 锟?

## 鐗堟湰鍒囨崲

`LogService.switch_maa_version()` 鏀寔锟?Stable / Beta / Alpha 涔嬮棿鍒囨崲 MAA 鐗堟湰锟?

1. 纭瀵硅瘽锟?
2. 璋冪敤 `UpdateCheckThread` 鏌ヨ
3. 寮瑰嚭 `UpdateDialog` 涓嬭浇鐩爣鐗堟湰
4. 涓嬭浇瀹屾垚鍚庤锟?`{MAA鐩綍}/` 涓嬬殑鏂囦欢
5. 鏇存柊浠撳簱鏉＄洰锟?`maa_version` 锟?`update_channel`
6. 閲嶆柊娉ㄥ叆 gui.json 閰嶇疆

## 涓嬭浇绾跨▼

`DownloadThread` 澶勭悊 MAA 鍘嬬缉鍖呯殑涓嬭浇鍜岃В鍘嬶細

```
涓嬭浇杩涘害 锟?progress(downloaded, total)
瑙ｅ帇 锟?涓存椂鐩綍 锟?瑕嗙洊鐩爣鐩綍 锟?娓呯悊涓存椂鏂囦欢
```

### 鏂囦欢瑕嗙洊绛栫暐

- **鐩綍**锛氬厛鍒犻櫎鐩爣鐩綍 锟?`shutil.copytree`
- **鏂囦欢**锛歚shutil.copy2`锛岃嫢鍙楁潈闄愰棶棰樺奖鍝嶅垯鍏堝鍒跺埌 `.new` 鍚庣紑鍐嶆浛锟?
- **鍙栨秷**锛氳锟?`cancel_flag`锛屼笅杞藉惊鐜腑妫€娴嬪苟閫€锟?

## UpdateDialog

涓嬭浇杩涘害瀵硅瘽妗嗭細

- 鏄剧ず鐗堟湰鍙枫€佹枃浠跺ぇ灏忥紙MB锟?
- 杩涘害鏉″疄鏃舵樉绀轰笅锟?瑙ｅ帇鐘讹拷?
- 瀹屾垚鍚庤嚜鍔ㄥ叧锟?

## 鎵归噺妫€鏌ユ洿锟?

`MaintService.check_updates()`锟?

1. 鏀堕泦鎵€锟?`maa_type != "general"` 鐨勪粨搴撴潯锟?
2. 鏌ヨ鏈€鏂扮増锟?
3. 鍒楀嚭鎵€鏈夌増鏈綆浜庢渶鏂扮殑绋嬪簭
4. 鎵归噺鎴栬€呴€愪釜纭涓嬭浇

## 鑷姩涓嬭浇 MAA

`MaintService.dl_maa()` 涓洪€変腑璐﹀彿涓嬭浇 MAA锟?

1. 璋冪敤 `UpdateCheckThread` 鏌ヨ鏈€鏂扮増
2. 寮瑰嚭 `UpdateDialog` 涓嬭浇锟?`accounts/{璐﹀彿ID}/MAA/`
3. 鎼滅储瑙ｅ帇鍚庣殑 `MAA.exe`
4. 鑷姩鍒涘缓浠撳簱鏉＄洰锛坄guard_enabled=True`锟?
5. 娉ㄥ叆閰嶇疆锛屾洿鏂拌处鍙蜂华琛ㄧ洏

## 鎵嬪姩缁戝畾

`MaintService.pk_maa()` 閫氳繃鏂囦欢閫夋嫨瀵硅瘽妗嗘墜鍔ㄧ粦瀹氬凡鏈夌殑 MAA 绋嬪簭锟?

- 鑷姩瑙ｆ瀽鐗堟湰鍙凤紙浠庣埗鐩綍鍚嶆彁锟?`vX.X.X`锟?
- 鍒涘缓浠撳簱鏉＄洰锛坄guard_enabled=False`锟?
- 娉ㄥ叆閰嶇疆

## maa-cli 瀹夎

`MaacliInstallThread` 锟?GitHub 涓嬭浇骞跺畨锟?maa-cli锟?

```
GET https://api.github.com/repos/MaaAssistantArknights/maa-cli/releases/latest
锟?杩囨护 windows x86_64 .zip 锟?涓嬭浇 锟?瑙ｅ帇锟?maa-cli/ 鐩綍
```

`MaacliInstallDialog` 鏄剧ず瀹夎杩涘害锛屽畬鎴愬悗鑷姩鍏抽棴锟?

## 浠ｇ悊鑷姩妫€锟?

`utils.setup_proxy()` 锟?`main.pyw` 鍚姩鏃惰皟鐢紝锟?`urllib.request` 閰嶇疆浠ｇ悊锟?

### 妫€娴嬮『锟?

1. **鐜鍙橀噺**锛氭锟?`HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy`锛屼换涓€瀛樺湪鍒欑洿鎺ヤ娇锟?
2. **绔彛鎺㈡祴**锛氫緷娆″皾锟?TCP 杩炴帴浠ヤ笅鏈湴绔彛锟?
   - 7890, 7891锛圕lash锟?
   - 1080, 10809锛坴2ray锟?
   - 8080锛堥€氱敤 HTTP 浠ｇ悊锟?
3. **瓒呮椂**锛氭瘡涓锟?0.3 锟?
4. **鍛戒腑**锛氳锟?`http://127.0.0.1:{port}` 浠ｇ悊

### 浣滅敤鑼冨洿

閰嶇疆鍚庯紝鎵€鏈夐€氳繃 `urllib.request` 鍙戝嚭鐨勮姹傦紙GitHub API銆佷笅杞斤級鍧囪蛋浠ｇ悊锟?

## MAAOrch 鑷洿锟?

### OrchUpdateCheckThread

`services/update_service.py` 涓殑 `OrchUpdateCheckThread` 鏌ヨ MAAOrch 锟?GitHub Release锟?

```
GET https://api.github.com/repos/xiachk083-hub/MAAOrch/releases/latest
```

杩斿洖 `tag_name`锛堝 `v1.2.0`锛夊拰涓嬭浇閾炬帴锟?

### 娴佺▼

1. **鑿滃崟瑙﹀彂**锛氬伐锟?锟?妫€锟?MAAOrch 鏇存柊
2. **鐗堟湰姣旇緝**锛歚_version_tuple()` 锟?`MainWindow.VERSION` 姣旇緝
3. **涓嬭浇**锛氫粠 GitHub 涓嬭浇 ZIP
4. **瑙ｅ帇**锛氳В鍘嬪埌椤圭洰鐩綍涓嬬殑 `_update/` 涓存椂鏂囦欢锟?
5. **鐢熸垚鏇挎崲鑴氭湰**锛氱敓锟?`replace.bat`
6. **閫€锟?+ 鏇挎崲**锛氬叧锟?MAAOrch 锟?鎵瑰鐞嗘潃锟?python 锟?澶嶅埗鏂囦欢 锟?閲嶅惎

### replace.bat 閫昏緫

```bat
taskkill /f /im python.exe
timeout /t 3
xcopy /E /Y "%~dp0_update\*" "%~dp0"
rmdir /S /Q "%~dp0_update"
start "" python "%~dp0main.pyw"
del "%~dp0replace.bat"
```

### 娉ㄦ剰浜嬮」

- 涓嶄細瑕嗙洊 `accounts/`銆乣config.json`銆乣backups/`锛坸copy 锟?`/exclude` 鎴栨墜鍔ㄦ帓闄わ級
- 闇€瑕佸湪鍏朵粬璁惧涓婂厛 `pip install PySide6`
