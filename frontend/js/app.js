// Frontend Authentication Handler for Khin Ticket

const isLocalDev = (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') && window.location.port !== '5000';
const API_URL = isLocalDev 
    ? `${window.location.protocol}//${window.location.hostname}:5000` 
    : window.location.origin;

function formatApiError(error) {
    if (error && (error.message === 'Failed to fetch' || error.name === 'TypeError')) {
        return isLocalDev
            ? 'Cannot connect to backend server. Please make sure the Python server is running on port 5000 (run: python run.py).'
            : 'Cannot connect to backend server. Please verify your connection or service status.';
    }
    return error.message || 'An unexpected error occurred.';
}

document.addEventListener('DOMContentLoaded', () => {
    // --- Container Elements ---
    const signinContainer = document.getElementById('signin-container');
    const signupContainer = document.getElementById('signup-container');
    const signupPasswordContainer = document.getElementById('signup-password-container');
    const signupOtpContainer = document.getElementById('signup-otp-container');

    // --- Alerts & Banners ---
    const verifiedSuccessBanner = document.getElementById('verified-success-banner');
    const signinUnverifiedAlert = document.getElementById('signin-unverified-alert');
    const linkResendSigninVerify = document.getElementById('link-resend-signin-verify');
    const linkGotoOtpVerify = document.getElementById('link-goto-otp-verify');

    // --- Step 3 OTP Elements ---
    const otpForm = document.getElementById('otp-form');
    const otpCodeInput = document.getElementById('signup-otp-code');
    const otpDisplayEmail = document.getElementById('otp-display-email');
    const btnResendOtp = document.getElementById('btn-resend-otp');
    const btnVerifyOtp = document.getElementById('btn-verify-otp');
    const btnBackToStep2 = document.getElementById('btn-back-to-step2');
    const linkOtpBackToStep1 = document.getElementById('link-otp-back-to-step1');
    const otpExpiryTimer = document.getElementById('otp-expiry-timer');

    // --- Switch Links & Navigation Buttons ---
    const linkToSignup = document.getElementById('link-to-signup');
    const linkToSignin = document.getElementById('link-to-signin');
    const linkStep2ToSignin = document.getElementById('link-step2-to-signin');
    const btnBackToStep1 = document.getElementById('btn-back-to-step1');

    // --- State Storage for 3-step Signup ---
    let pendingSignupData = null;
    let resendInterval = null;
    let expiryInterval = null;

    // View Switching Functions
    function stopAllTimers() {
        if (resendInterval) {
            clearInterval(resendInterval);
            resendInterval = null;
        }
        if (expiryInterval) {
            clearInterval(expiryInterval);
            expiryInterval = null;
        }
    }

    function showSignIn() {
        if (signinContainer) signinContainer.classList.add('active');
        if (signupContainer) signupContainer.classList.remove('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.remove('active');
        if (signupOtpContainer) signupOtpContainer.classList.remove('active');
        stopAllTimers();
    }

    function showSignUpStep1() {
        if (signinContainer) signinContainer.classList.remove('active');
        if (signupContainer) signupContainer.classList.add('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.remove('active');
        if (signupOtpContainer) signupOtpContainer.classList.remove('active');
        stopAllTimers();
    }

    function showSignUpStep2() {
        if (signinContainer) signinContainer.classList.remove('active');
        if (signupContainer) signupContainer.classList.remove('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.add('active');
        if (signupOtpContainer) signupOtpContainer.classList.remove('active');
        stopAllTimers();
    }

    function showSignUpStep3(email) {
        if (signinContainer) signinContainer.classList.remove('active');
        if (signupContainer) signupContainer.classList.remove('active');
        if (signupPasswordContainer) signupPasswordContainer.classList.remove('active');
        if (signupOtpContainer) signupOtpContainer.classList.add('active');

        const displayMail = email || (pendingSignupData ? pendingSignupData.email : '');
        if (otpDisplayEmail && displayMail) {
            otpDisplayEmail.textContent = displayMail;
        }
        if (otpCodeInput) {
            otpCodeInput.value = '';
            setTimeout(() => otpCodeInput.focus(), 100);
        }
    }

    // Helper for Resend Countdown
    function startResendCountdown(seconds = 60) {
        if (!btnResendOtp) return;
        if (resendInterval) clearInterval(resendInterval);
        
        let remaining = seconds;
        btnResendOtp.disabled = true;
        btnResendOtp.innerHTML = `Resend in <span id="resend-countdown">${remaining}</span>s`;

        resendInterval = setInterval(() => {
            remaining--;
            const countEl = document.getElementById('resend-countdown');
            if (countEl) countEl.textContent = remaining;
            if (remaining <= 0) {
                clearInterval(resendInterval);
                resendInterval = null;
                btnResendOtp.disabled = false;
                btnResendOtp.textContent = 'Resend verification code';
            }
        }, 1000);
    }

    // Helper for 10-minute expiry countdown
    function startExpiryCountdown(seconds = 600) {
        if (!otpExpiryTimer) return;
        if (expiryInterval) clearInterval(expiryInterval);

        let remaining = seconds;
        const updateDisplay = () => {
            const m = Math.floor(remaining / 60);
            const s = remaining % 60;
            otpExpiryTimer.textContent = `${m}:${s < 10 ? '0' : ''}${s}`;
        };
        updateDisplay();

        expiryInterval = setInterval(() => {
            remaining--;
            if (remaining <= 0) {
                clearInterval(expiryInterval);
                expiryInterval = null;
                otpExpiryTimer.textContent = 'Expired';
                showToast('Verification code has expired. Please request a new code.', 'error');
            } else {
                updateDisplay();
            }
        }, 1000);
    }

    // OTP Code Input Formatter (numbers only, max 6 digits)
    if (otpCodeInput) {
        otpCodeInput.addEventListener('input', (e) => {
            e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6);
        });
    }

    // Switch Event Listeners
    if (linkToSignup) {
        linkToSignup.addEventListener('click', (e) => {
            e.preventDefault();
            showSignUpStep1();
            clearForms();
            pendingSignupData = null;
        });
    }

    if (linkToSignin) {
        linkToSignin.addEventListener('click', (e) => {
            e.preventDefault();
            showSignIn();
            clearForms();
            pendingSignupData = null;
        });
    }

    if (linkStep2ToSignin) {
        linkStep2ToSignin.addEventListener('click', (e) => {
            e.preventDefault();
            showSignIn();
            clearForms();
            pendingSignupData = null;
        });
    }

    if (btnBackToStep1) {
        btnBackToStep1.addEventListener('click', (e) => {
            e.preventDefault();
            showSignUpStep1();
        });
    }

    if (btnBackToStep2) {
        btnBackToStep2.addEventListener('click', (e) => {
            e.preventDefault();
            showSignUpStep2();
        });
    }

    if (linkOtpBackToStep1) {
        linkOtpBackToStep1.addEventListener('click', (e) => {
            e.preventDefault();
            showSignUpStep1();
        });
    }

    // --- Password Visibility Toggles ---
    setupPasswordToggle('signin-password', 'toggle-signin-password');
    setupPasswordToggle('signup-password', 'toggle-signup-password');
    setupPasswordToggle('signup-confirm-password', 'toggle-signup-confirm-password');

    // --- Form Elements ---
    const signinForm = document.getElementById('signin-form');
    const signupForm = document.getElementById('signup-form');
    const passwordForm = document.getElementById('password-form');

    // ==========================================
    // 1. SIGN IN HANDLER
    // ==========================================
    if (signinForm) {
        signinForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const emailInput = document.getElementById('signin-email');
            const passwordInput = document.getElementById('signin-password');
            const btnSignin = document.getElementById('btn-signin');

            const email = emailInput ? emailInput.value.trim().toLowerCase() : '';
            const password = passwordInput ? passwordInput.value : '';

            if (verifiedSuccessBanner) verifiedSuccessBanner.style.display = 'none';
            if (signinUnverifiedAlert) signinUnverifiedAlert.style.display = 'none';

            if (!email) {
                showToast('Please enter your email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!validateEmail(email)) {
                showToast('Please enter a valid email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!password) {
                showToast('Please enter your password.', 'error');
                if (passwordInput) passwordInput.focus();
                return;
            }

            try {
                setLoading(btnSignin, true, 'Signing In...');
                
                const response = await fetch(`${API_URL}/api/auth/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });

                let data;
                try {
                    data = await response.json();
                } catch (jsonErr) {
                    throw new Error(`Failed to parse server response (Status ${response.status}). Please verify the backend service is running.`);
                }

                if (!response.ok) {
                    // Check if error is due to unverified email
                    if (response.status === 403 || (data.detail && data.detail.toLowerCase().includes('not been verified'))) {
                        if (signinUnverifiedAlert) signinUnverifiedAlert.style.display = 'block';
                        showToast(data.detail || 'Your account email has not been verified yet. Please check your email.', 'error');
                        setLoading(btnSignin, false, 'Sign In');
                        return;
                    }
                    throw new Error(data.detail || 'Authentication failed. Please check your credentials.');
                }

                // Store Token & User Info
                localStorage.setItem('access_token', data.access_token);
                if (data.user) {
                    localStorage.setItem('user_role', data.user.role || 'employee');
                    localStorage.setItem('user_name', data.user.full_name || '');
                    localStorage.setItem('user_email', data.user.email || '');
                    localStorage.setItem('user_department', data.user.department || 'General');
                    localStorage.setItem('user_position', data.user.position || 'Employee');
                    localStorage.setItem('can_manage_departments', data.user.can_manage_departments ? 'true' : 'false');
                }
                showToast('Sign-in successful! Redirecting...', 'success');
                
                // Role-based routing
                setTimeout(() => {
                    const role = data.user ? (data.user.role || 'employee') : 'employee';
                    const canManage = data.user && Boolean(data.user.can_manage_departments);
                    const staffRoles = ['super_admin', 'admin', 'tech_member', 'agent', 'dept_lead', 'admin_lead', 'dept_agent', 'dept_member'];
                    if (staffRoles.includes(role) || canManage) {
                        window.location.href = 'dashboard.html';
                    } else {
                        window.location.href = 'portal.html';
                    }
                }, 1000);

            } catch (error) {
                console.error('Sign-in error:', error);
                showToast(formatApiError(error), 'error');
                setLoading(btnSignin, false, 'Sign In');
            }
        });
    }

    // Unverified account actions in signin alert
    if (linkGotoOtpVerify) {
        linkGotoOtpVerify.addEventListener('click', (e) => {
            e.preventDefault();
            const signinEmailInput = document.getElementById('signin-email');
            const email = signinEmailInput ? signinEmailInput.value.trim().toLowerCase() : '';

            if (!email || !validateEmail(email)) {
                showToast('Please enter your valid email address in the field above.', 'error');
                if (signinEmailInput) signinEmailInput.focus();
                return;
            }

            pendingSignupData = { email };
            showSignUpStep3(email);
            startExpiryCountdown(600);
        });
    }

    if (linkResendSigninVerify) {
        linkResendSigninVerify.addEventListener('click', async (e) => {
            e.preventDefault();
            const signinEmailInput = document.getElementById('signin-email');
            const email = signinEmailInput ? signinEmailInput.value.trim().toLowerCase() : '';

            if (!email || !validateEmail(email)) {
                showToast('Please enter your valid email address in the field above first.', 'error');
                if (signinEmailInput) signinEmailInput.focus();
                return;
            }

            try {
                linkResendSigninVerify.disabled = true;
                linkResendSigninVerify.textContent = 'Sending...';

                const response = await fetch(`${API_URL}/api/auth/resend-registration-otp`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });

                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to resend verification code.');
                }

                pendingSignupData = { email };
                showSignUpStep3(email);
                startResendCountdown(60);
                startExpiryCountdown(600);
                showToast(data.message || `A new verification code was sent to ${email}`, 'success');
            } catch (err) {
                console.error('Resend verification error:', err);
                showToast(formatApiError(err), 'error');
                linkResendSigninVerify.disabled = false;
                linkResendSigninVerify.textContent = 'Resend code';
            }
        });
    }

    // ==========================================
    // 2. SIGN UP STEP 1 HANDLER (Profile & Role)
    // ==========================================
    if (signupForm) {
        signupForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const fullnameInput = document.getElementById('signup-fullname');
            const emailInput = document.getElementById('signup-email');
            const departmentInput = document.getElementById('signup-department');
            const positionInput = document.getElementById('signup-position');

            const fullName = fullnameInput ? fullnameInput.value.trim() : '';
            const email = emailInput ? emailInput.value.trim().toLowerCase() : '';
            const department = departmentInput ? departmentInput.value.trim() : 'General';
            const position = positionInput ? positionInput.value.trim() : 'Employee';

            if (!fullName) {
                showToast('Please enter your full name.', 'error');
                if (fullnameInput) fullnameInput.focus();
                return;
            }

            if (!email) {
                showToast('Please enter your email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!validateEmail(email)) {
                showToast('Please enter a valid email address.', 'error');
                if (emailInput) emailInput.focus();
                return;
            }

            if (!position) {
                showToast('Please enter your position or job title.', 'error');
                if (positionInput) positionInput.focus();
                return;
            }

            // Save Step 1 state
            pendingSignupData = {
                fullName,
                email,
                department,
                position
            };

            // Personalize Step 2 card
            const passwordSubtitle = document.getElementById('password-subtitle');
            if (passwordSubtitle) {
                passwordSubtitle.textContent = `Set personal password for ${email}`;
            }

            // Navigate to Step 2
            showSignUpStep2();
            const signupPasswordInput = document.getElementById('signup-password');
            if (signupPasswordInput) signupPasswordInput.focus();
        });
    }

    // ==========================================
    // 3. SIGN UP STEP 2 HANDLER (Password & Send Verification OTP)
    // ==========================================
    if (passwordForm) {
        passwordForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            if (!pendingSignupData) {
                showToast('Registration session lost. Please complete Step 1 first.', 'error');
                showSignUpStep1();
                return;
            }

            const passwordInput = document.getElementById('signup-password');
            const confirmPasswordInput = document.getElementById('signup-confirm-password');
            const btnCompleteSignup = document.getElementById('btn-complete-signup');

            const password = passwordInput ? passwordInput.value : '';
            const confirmPassword = confirmPasswordInput ? confirmPasswordInput.value : '';

            if (!password) {
                showToast('Please enter an account password.', 'error');
                if (passwordInput) passwordInput.focus();
                return;
            }

            if (password.length < 6) {
                showToast('Password must be at least 6 characters long.', 'error');
                if (passwordInput) passwordInput.focus();
                return;
            }

            if (password !== confirmPassword) {
                showToast('Passwords do not match. Please re-enter and confirm.', 'error');
                if (confirmPasswordInput) {
                    confirmPasswordInput.focus();
                    confirmPasswordInput.select();
                }
                return;
            }

            // Dispatch 6-digit verification code to email
            try {
                setLoading(btnCompleteSignup, true, 'Sending Code...');

                const response = await fetch(`${API_URL}/api/auth/send-registration-otp`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        full_name: pendingSignupData.fullName,
                        email: pendingSignupData.email,
                        department: pendingSignupData.department,
                        position: pendingSignupData.position,
                        password: password
                    })
                });

                let data;
                try {
                    data = await response.json();
                } catch (jsonErr) {
                    throw new Error(`Failed to parse server response (Status ${response.status}). Please verify the backend service is reachable.`);
                }

                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to send verification code. Please try again.');
                }

                // Transition to Step 3: Manual 6-Digit Code Entry
                showSignUpStep3(pendingSignupData.email);
                startResendCountdown(60);
                startExpiryCountdown(600);
                showToast(data.message || `Verification code sent to ${pendingSignupData.email}! Please check your email.`, 'success');
                setLoading(btnCompleteSignup, false, 'Continue to Verification \u2192');

            } catch (error) {
                console.error('Send OTP error:', error);
                showToast(formatApiError(error), 'error');
                setLoading(btnCompleteSignup, false, 'Continue to Verification \u2192');
            }
        });
    }

    // ==========================================
    // 4. SIGN UP STEP 3 HANDLER (Verify Manual 6-Digit OTP Code)
    // ==========================================
    if (otpForm) {
        otpForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const targetEmail = (pendingSignupData && pendingSignupData.email) 
                ? pendingSignupData.email 
                : (otpDisplayEmail ? otpDisplayEmail.textContent.trim() : '');

            if (!targetEmail || !validateEmail(targetEmail)) {
                showToast('Registration session lost. Please complete Step 1 first.', 'error');
                showSignUpStep1();
                return;
            }

            const otpCode = otpCodeInput ? otpCodeInput.value.trim().replace(/\s+/g, '') : '';
            if (!otpCode || otpCode.length !== 6) {
                showToast('Please enter the 6-digit verification code.', 'error');
                if (otpCodeInput) otpCodeInput.focus();
                return;
            }

            try {
                setLoading(btnVerifyOtp, true, 'Verifying...');

                const response = await fetch(`${API_URL}/api/auth/verify-registration-otp`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: targetEmail,
                        otp_code: otpCode
                    })
                });

                let data;
                try {
                    data = await response.json();
                } catch (jsonErr) {
                    throw new Error(`Failed to parse server response (Status ${response.status}). Please verify the backend service is reachable.`);
                }

                if (!response.ok) {
                    throw new Error(data.detail || 'Verification failed. Please check the code and try again.');
                }

                stopAllTimers();

                // Store Token & User Info
                if (data.access_token) {
                    localStorage.setItem('access_token', data.access_token);
                }
                if (data.user) {
                    localStorage.setItem('user_role', data.user.role || 'employee');
                    localStorage.setItem('user_name', data.user.full_name || '');
                    localStorage.setItem('user_email', data.user.email || '');
                    localStorage.setItem('user_department', data.user.department || 'General');
                    localStorage.setItem('user_position', data.user.position || 'Employee');
                    localStorage.setItem('can_manage_departments', data.user.can_manage_departments ? 'true' : 'false');
                }

                showToast('Account verified and created successfully! Redirecting...', 'success');

                setTimeout(() => {
                    const role = data.user ? (data.user.role || 'employee') : 'employee';
                    const canManage = data.user && Boolean(data.user.can_manage_departments);
                    const staffRoles = ['super_admin', 'admin', 'tech_member', 'agent', 'dept_lead', 'admin_lead', 'dept_agent', 'dept_member'];
                    if (staffRoles.includes(role) || canManage) {
                        window.location.href = 'dashboard.html';
                    } else {
                        window.location.href = 'portal.html';
                    }
                }, 1000);

            } catch (error) {
                console.error('OTP verification error:', error);
                showToast(formatApiError(error), 'error');
                setLoading(btnVerifyOtp, false, 'Verify & Create Account \u2192');
            }
        });
    }

    // Resend OTP Button in Step 3
    if (btnResendOtp) {
        btnResendOtp.addEventListener('click', async (e) => {
            e.preventDefault();
            const targetEmail = (pendingSignupData && pendingSignupData.email) 
                ? pendingSignupData.email 
                : (otpDisplayEmail ? otpDisplayEmail.textContent.trim() : '');

            if (!targetEmail || !validateEmail(targetEmail)) {
                showToast('No valid email found to resend to.', 'error');
                return;
            }

            try {
                btnResendOtp.disabled = true;
                btnResendOtp.textContent = 'Sending...';

                const response = await fetch(`${API_URL}/api/auth/resend-registration-otp`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: targetEmail })
                });

                let data;
                try {
                    data = await response.json();
                } catch (jsonErr) {
                    throw new Error(`Failed to parse server response (Status ${response.status}).`);
                }

                if (!response.ok) {
                    throw new Error(data.detail || 'Failed to resend verification code.');
                }

                showToast(data.message || `A new verification code was sent to ${targetEmail}.`, 'success');
                startResendCountdown(60);
                startExpiryCountdown(600);
                if (otpCodeInput) {
                    otpCodeInput.value = '';
                    otpCodeInput.focus();
                }
            } catch (error) {
                console.error('Resend OTP error:', error);
                showToast(formatApiError(error), 'error');
                btnResendOtp.disabled = false;
                btnResendOtp.textContent = 'Resend verification code';
            }
        });
    }

    // ==========================================
    // 5. QUERY PARAMS VERIFICATION HANDLING
    // ==========================================
    const urlParams = new URLSearchParams(window.location.search);
    const verifyStatus = urlParams.get('verify_status');
    const verifyEmail = urlParams.get('email');

    if (verifyStatus === 'success') {
        showSignIn();
        if (verifiedSuccessBanner) verifiedSuccessBanner.style.display = 'flex';
        if (signinUnverifiedAlert) signinUnverifiedAlert.style.display = 'none';
        if (verifyEmail) {
            const signinEmailInput = document.getElementById('signin-email');
            if (signinEmailInput) signinEmailInput.value = verifyEmail;
            const signinPasswordInput = document.getElementById('signin-password');
            if (signinPasswordInput) signinPasswordInput.focus();
        }
        showToast('Your email has been verified! You can now sign in.', 'success');
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (verifyStatus === 'expired') {
        showSignIn();
        if (signinUnverifiedAlert) signinUnverifiedAlert.style.display = 'block';
        if (verifiedSuccessBanner) verifiedSuccessBanner.style.display = 'none';
        if (verifyEmail) {
            const signinEmailInput = document.getElementById('signin-email');
            if (signinEmailInput) signinEmailInput.value = verifyEmail;
        }
        showToast('Your verification link has expired. Please click below to resend.', 'error');
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (verifyStatus === 'invalid' || verifyStatus === 'error') {
        showSignIn();
        showToast('Invalid or expired verification link. Please sign in or request a new link.', 'error');
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (urlParams.get('expired') === 'true') {
        showToast('Your session has expired. Please sign in again.', 'info');
        window.history.replaceState({}, document.title, window.location.pathname);
    }
});

// --- Utility Helpers ---

function setupPasswordToggle(inputId, buttonId) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(buttonId);
    
    if (!input || !button) return;

    button.addEventListener('click', () => {
        const isPassword = input.getAttribute('type') === 'password';
        const type = isPassword ? 'text' : 'password';
        input.setAttribute('type', type);
        
        const eyeIcon = button.querySelector('.eye-icon');
        if (eyeIcon) {
            if (type === 'text') {
                eyeIcon.innerHTML = `
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                    <line x1="1" y1="1" x2="23" y2="23" />
                `;
            } else {
                eyeIcon.innerHTML = `
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                `;
            }
        }
    });
}

function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

function setLoading(button, isLoading, text) {
    if (!button) return;
    if (isLoading) {
        button.disabled = true;
        button.innerHTML = `<span class="spinner"></span> <span>${text}</span>`;
    } else {
        button.disabled = false;
        button.innerHTML = `<span>${text}</span>`;
    }
}

function clearForms() {
    const signinForm = document.getElementById('signin-form');
    const signupForm = document.getElementById('signup-form');
    const passwordForm = document.getElementById('password-form');
    const otpForm = document.getElementById('otp-form');
    const verifiedSuccessBanner = document.getElementById('verified-success-banner');
    const signinUnverifiedAlert = document.getElementById('signin-unverified-alert');

    if (signinForm) signinForm.reset();
    if (signupForm) signupForm.reset();
    if (passwordForm) passwordForm.reset();
    if (otpForm) otpForm.reset();
    if (verifiedSuccessBanner) verifiedSuccessBanner.style.display = 'none';
    if (signinUnverifiedAlert) signinUnverifiedAlert.style.display = 'none';
}

// Toast Helper
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    // Choose appropriate SVG Icon based on type
    let iconSvg = '';
    if (type === 'success') {
        iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
    } else if (type === 'error') {
        iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
    } else {
        iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
    }

    toast.innerHTML = `${iconSvg} <span>${message}</span>`;
    container.appendChild(toast);
    
    // Auto remove after 4 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
