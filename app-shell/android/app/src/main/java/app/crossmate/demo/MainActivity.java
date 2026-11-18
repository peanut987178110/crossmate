package app.crossmate.demo;

import android.content.pm.ApplicationInfo;
import android.os.Bundle;
import android.util.Log;
import android.webkit.WebSettings;
import android.webkit.WebView;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

/**
 * CrossMate 原生壳入口。
 *
 * 后端（FastAPI）由 Chaquopy 打包进 APK，App 启动时在本机 127.0.0.1 上跑起来，
 * 因此装到手机上**不依赖电脑、不依赖网络**，飞行模式也能用。
 *
 * 地址注入：app.js 的 const API 在模块加载时定死，所以必须在它之前写好
 * meta[name=cm-api]。链路是「原生写 localStorage.cm_api → index.html <head> 里
 * 同步执行的 js/shell.js 写回 meta → app.js 才读」。
 * 默认值已由 scripts/sync-web.mjs 写进 meta[name=cm-api-default]，首次启动即可用。
 *
 * 仍然保留 -e cm_api 覆盖，方便对着电脑上的后端联调：
 *   adb shell am start -n app.crossmate.demo/.MainActivity -e cm_api "http://192.168.1.23:8000/api/v0.2"
 */
public class MainActivity extends BridgeActivity {

    private static final String TAG = "CrossMate";
    private static final int PY_PORT = 8765;
    private static final String LOCAL_API = "http://127.0.0.1:" + PY_PORT + "/api/v0.2";

    /** 后端是否就绪；页面可能先于它加载完，所以要记下来在 onPageFinished 里补写。 */
    private volatile boolean backendReady = false;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        startPythonBackend();
    }

    /** 在后台线程把内置后端拉起来，不阻塞 UI。 */
    private void startPythonBackend() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                PyObject boot = Python.getInstance().getModule("crossmate_boot");
                PyObject r = boot.callAttr("start", getFilesDir().getAbsolutePath());
                String info = r.toString();  // {"port": 8765, "error": ""}（dict 的 toString）
                backendReady = true;
                Log.i(TAG, "python backend: " + info);
                if (boot.callAttr("wait_ready", 30).toBoolean()) {
                    Log.i(TAG, "backend ready");
                    injectApi();
                } else {
                    Log.e(TAG, "backend did not become ready in time: " + info);
                }
            } catch (Exception e) {
                Log.e(TAG, "python backend failed", e);
            }
        }, "cm-python-boot").start();
    }

    @Override
    public void onStart() {
        super.onStart();
        Bridge bridge = getBridge();
        if (bridge == null || bridge.getWebView() == null) return;

        // WebView 默认白底：系统启动画面退场后、页面首帧绘出来之前会露一帧白。
        // 设成品牌深色，和启动页/页面背景连成一色，冷启动不再闪白。
        bridge.getWebView().setBackgroundColor(0xFF0D0A13);

        WebSettings s = bridge.getWebView().getSettings();
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setMediaPlaybackRequiresUserGesture(false);
        // 页面加载完成后再注入一次，覆盖「页面比后端先就绪」的情况
        bridge.setWebViewClient(new BridgeWebViewClient(bridge) {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                injectApi();
            }
        });
    }

    /** 写入后端地址。必须在本机服务起来之后再写，否则页面会连到不存在的端口。 */
    private void injectApi() {
        Bridge bridge = getBridge();
        if (bridge == null || bridge.getWebView() == null || !backendReady) return;
        String api = getIntent().getStringExtra("cm_api");
        if (api == null || api.isEmpty()) {
            api = LOCAL_API;
        }
        final String js = "try{localStorage.setItem('cm_api'," + toJsString(api) + ");}catch(e){}";
        // WebView 只能在主线程操作：这里可能来自 python 启动线程。
        // 页面可能还没加载出 <head>（此时写入会被后续导航冲掉），所以连写几次兜底。
        WebView wv = bridge.getWebView();
        for (int i = 0; i < 6; i++) {
            final long delay = i * 1500L;
            wv.postDelayed(() -> wv.evaluateJavascript(js, null), delay);
        }
    }

    private boolean isDebuggable() {
        return (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
    }

    private static String toJsString(String v) {
        return "\"" + v.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}