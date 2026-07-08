(function(){
  const api = window.faustAppUI;
  if (!api) return;

  console.log('Frontend Example Plugin: faustAppUI is available, injecting app hook...');

  api.on('chat_message', function(message){
    if (!message || message.type !== 'done') return;
    api.showBubble('插件已监听到一次聊天完成事件', 'ai');
  });
})();