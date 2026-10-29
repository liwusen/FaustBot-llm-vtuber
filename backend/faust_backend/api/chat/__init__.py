"""聊天与音频相关的 HTTP/WS 路由（原 ``faust_backend/routes/`` 的 chat 组）。

⚠ 本包名与其中的 ``chat`` 模块同名，这是 CPython 的一个坑：

    import faust_backend.api.chat.chat as chat        # ✗ ImportError
    import faust_backend.api.chat.audio as audio      # ✗ 也会连带失败

原因：只要有代码写 ``from faust_backend.api.chat import chat``（不带 as 别名），
``faust_backend.api`` 的 ``chat`` 属性就会被重绑成 ``chat.py`` 模块，
之后再按属性链走 ``faust_backend.api.chat.<子模块>`` 就会找错模块。

约定：
- 路由聚合处（``api/__init__.py``）一律用 ``import x as 别名`` 的形式，禁止裸写
  ``from faust_backend.api.chat import chat``；
- 其它地方用 ``from faust_backend.api.chat import chat as chat``（显式别名）。
``api/system/system.py`` 有同样的坑。
"""
