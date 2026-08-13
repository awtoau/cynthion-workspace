#[doc = r"Enumeration of all the interrupts."]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
#[repr(u16)]
pub enum Interrupt {
    #[doc = "1 - CONSOLE"]
    CONSOLE = 1,
    #[doc = "2 - APOLLO_UART"]
    APOLLO_UART = 2,
    #[doc = "3 - BOARD_I2C"]
    BOARD_I2C = 3,
    #[doc = "4 - BOARD_I2C_MUX_TARGET"]
    BOARD_I2C_MUX_TARGET = 4,
    #[doc = "5 - BOARD_I2C_MUX_AUX"]
    BOARD_I2C_MUX_AUX = 5,
    #[doc = "6 - BOARD_I2C_MUX_POWER_ALERT"]
    BOARD_I2C_MUX_POWER_ALERT = 6,
    #[doc = "8 - BOARD_I2C_MUX_TARGET_FAULT"]
    BOARD_I2C_MUX_TARGET_FAULT = 8,
    #[doc = "9 - BOARD_I2C_MUX_AUX_FAULT"]
    BOARD_I2C_MUX_AUX_FAULT = 9,
    #[doc = "13 - BOARD_GPIO_BUTTON"]
    BOARD_GPIO_BUTTON = 13,
    #[doc = "17 - BOARD_CLOCKS_PLL_LOSS"]
    BOARD_CLOCKS_PLL_LOSS = 17,
}
#[doc = r" TryFromInterruptError"]
#[derive(Debug, Copy, Clone)]
pub struct TryFromInterruptError(());
impl Interrupt {
    #[doc = r" Attempt to convert a given value into an `Interrupt`"]
    #[inline]
    pub fn try_from(value: u8) -> Result<Self, TryFromInterruptError> {
        match value {
            1 => Ok(Interrupt::CONSOLE),
            2 => Ok(Interrupt::APOLLO_UART),
            3 => Ok(Interrupt::BOARD_I2C),
            4 => Ok(Interrupt::BOARD_I2C_MUX_TARGET),
            5 => Ok(Interrupt::BOARD_I2C_MUX_AUX),
            6 => Ok(Interrupt::BOARD_I2C_MUX_POWER_ALERT),
            8 => Ok(Interrupt::BOARD_I2C_MUX_TARGET_FAULT),
            9 => Ok(Interrupt::BOARD_I2C_MUX_AUX_FAULT),
            13 => Ok(Interrupt::BOARD_GPIO_BUTTON),
            17 => Ok(Interrupt::BOARD_CLOCKS_PLL_LOSS),
            _ => Err(TryFromInterruptError(())),
        }
    }
}
