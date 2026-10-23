import type { UserValidateEvent } from '@netlify/functions';

// The five accounts are created through the server-side admin API only.
export default {
  userValidate(event: UserValidateEvent) { return event.deny(); },
};
