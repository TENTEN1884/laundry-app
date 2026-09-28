importScripts('https://www.gstatic.com/firebasejs/10.13.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.13.1/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: 'AIzaSyBeXUAw5CVI3OQ6_fXSV4REaxaFfePSLFA',
  authDomain: 'laundryapp-26dec.firebaseapp.com',
  projectId: 'laundryapp-26dec',
  storageBucket: 'laundryapp-26dec.firebasestorage.app',
  messagingSenderId: '521493217',
  appId: '1:521493217:web:eb13e38e690aaa3308b5a0',
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  const title = (payload.notification && payload.notification.title) || '세탁실 알림';
  const body = (payload.notification && payload.notification.body) || '';
  self.registration.showNotification(title, { body: body });
});
