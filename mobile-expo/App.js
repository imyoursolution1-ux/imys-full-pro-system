import React from 'react';
import { SafeAreaView, StatusBar, Linking } from 'react-native';
import { WebView } from 'react-native-webview';

const WEBSITE_URL = 'https://imyoursolution.com';

export default function App() {
  return (
    <SafeAreaView style={{ flex: 1 }}>
      <StatusBar barStyle="dark-content" />
      <WebView
        source={{ uri: WEBSITE_URL }}
        style={{ flex: 1 }}
        javaScriptEnabled
        domStorageEnabled
        originWhitelist={["*"]}
        setSupportMultipleWindows={false}
        onShouldStartLoadWithRequest={(request) => {
          const url = request.url || '';
          if (url.startsWith('whatsapp://') || url.includes('api.whatsapp.com') || url.includes('web.whatsapp.com') || url.startsWith('upi://')) {
            Linking.openURL(url).catch(() => {});
            return false;
          }
          return true;
        }}
      />
    </SafeAreaView>
  );
}
