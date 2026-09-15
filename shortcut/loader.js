(function(){
  var u = 'https://raw.githubusercontent.com/AssiamahS/swiper/main/swiper.js?t=' + Date.now();
  var x = new XMLHttpRequest();
  x.onreadystatechange = function(){
    if (x.readyState !== 4) return;
    if (x.status === 200) {
      try { (0, eval)(x.responseText); completion('swiper loaded'); }
      catch (e) { completion('swiper error: ' + e); }
    } else { completion('swiper fetch failed: ' + x.status); }
  };
  x.open('GET', u); x.send();
})();
